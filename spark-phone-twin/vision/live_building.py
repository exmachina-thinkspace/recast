"""Live building view: plan geometry + camera clouds + camera frustums + live people.

Fixes over v1:
  * orbit pivots on a movable target, so rotation still works zoomed in
  * scroll direction inverted (wheel up = zoom in)
  * point splats scale with zoom, so zooming stops looking pixelated
  * people drawn as bright enlarged splats with a ground marker + halo
  * each camera's view frustum drawn on the map

  drag=orbit  scroll=zoom  WASD=pan  f=frustums  c=clouds  p=people-only  r=reset  q=quit
"""
import os, time, threading
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import numpy as np
import cv2

PLANS = os.path.expanduser("~/plans")
FRAMES = os.path.expanduser("~/arlo-frames")
CEIL = 9 * 0.3048 + 7 * 0.0254
F2F = CEIL + 0.60
OW, OH = 1500, 900
STRIDE, PERSON, HFOV = 3, 0, 120.0

CAMS = {
    "common": dict(level="level1", url=os.environ.get("RTSP_COMMON", ""),
                   fx=0.35, fy=0.45, yaw=0.0,  label="1F COMMON AREA", col=(120, 255, 120)),
    "lobby":  dict(level="level2", url=os.environ.get("RTSP_LOBBY", ""),
                   fx=0.40, fy=0.55, yaw=0.0,  label="2F LOBBY",       col=(120, 200, 255)),
    "swhall": dict(level="level2", url=os.environ.get("RTSP_SWHALL", ""),
                   fx=0.68, fy=0.30, yaw=90.0, label="2F SW HALLWAY",  col=(255, 160, 255)),
}

walls = {n: np.load("%s/%s_walls_m.npy" % (PLANS, n)) for n in ("level1", "level2")
         if os.path.exists("%s/%s_walls_m.npy" % (PLANS, n))}
bx0 = min(w[:, [0, 2]].min() for w in walls.values())
bx1 = max(w[:, [0, 2]].max() for w in walls.values())
by0 = min(w[:, [1, 3]].min() for w in walls.values())
by1 = max(w[:, [1, 3]].max() for w in walls.values())
SPAN = max(bx1 - bx0, by1 - by0)
HOME = np.array([(bx0 + bx1) / 2, (by0 + by1) / 2, F2F / 2], np.float32)

view = dict(yaw=35.0, pitch=58.0, dist=SPAN * 1.05, target=HOME.copy(),
            drag=False, x=0, y=0, clouds=True, frustums=True, people_only=False)


def on_mouse(e, x, y, flags, _):
    if e == cv2.EVENT_LBUTTONDOWN:
        view.update(drag=True, x=x, y=y)
    elif e == cv2.EVENT_LBUTTONUP:
        view["drag"] = False
    elif e == cv2.EVENT_MOUSEMOVE and view["drag"]:
        view["yaw"] += (x - view["x"]) * 0.4
        view["pitch"] = float(np.clip(view["pitch"] - (y - view["y"]) * 0.3, 3, 89))
        view.update(x=x, y=y)
    elif e == cv2.EVENT_MOUSEWHEEL:
        # wheel UP = zoom IN
        view["dist"] = float(np.clip(view["dist"] * (1 / 1.15 if flags > 0 else 1.15),
                                     1.5, SPAN * 4))


def basis():
    a, b = np.radians(view["yaw"]), np.radians(view["pitch"])
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]], np.float32)
    Rx = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]], np.float32)
    return Rx @ Rz


def make_view():
    R = basis()
    cam = np.array([0, 0, view["dist"]], np.float32)
    f = OW / 1.5
    T = view["target"]
    # splat radius grows as we approach; keeps surfaces solid instead of dotty
    rad = int(np.clip(round(1.6 * SPAN / max(view["dist"], 1e-3)), 1, 6))
    return R, cam, f, T, rad


def proj(P, R, cam, f, T):
    q = (P - T) @ R.T + cam
    m = q[:, 2] > 0.25
    q = q[m]
    u = (f * q[:, 0] / q[:, 2] + OW / 2).astype(np.int32)
    v = (OH / 2 - f * q[:, 1] / q[:, 2]).astype(np.int32)
    ok = (u >= 0) & (u < OW) & (v >= 0) & (v < OH)
    return u[ok], v[ok], q[ok, 2], m, ok


def splat(img, u, v, col, rad):
    if rad <= 1:
        img[v, u] = col
        return
    for dy in range(-rad + 1, rad):
        for dx in range(-rad + 1, rad):
            uu = np.clip(u + dx, 0, OW - 1)
            vv = np.clip(v + dy, 0, OH - 1)
            img[vv, uu] = col


def line3(img, a, b, R, cam, f, T, col, th=1):
    pa = (a - T) @ R.T + cam
    pb = (b - T) @ R.T + cam
    if pa[2] <= 0.25 or pb[2] <= 0.25:
        return
    cv2.line(img,
             (int(f * pa[0] / pa[2] + OW / 2), int(OH / 2 - f * pa[1] / pa[2])),
             (int(f * pb[0] / pb[2] + OW / 2), int(OH / 2 - f * pb[1] / pb[2])),
             col, th, cv2.LINE_AA)


def load_ply(p):
    with open(p, "rb") as fh:
        h = b""
        while b"end_header\n" not in h:
            h += fh.read(1)
        nv = int([l for l in h.decode().splitlines()
                  if l.startswith("element vertex")][0].split()[-1])
        v = np.frombuffer(fh.read(nv * 15), dtype=[
            ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
            ("r", "u1"), ("g", "u1"), ("b", "u1")], count=nv)
    return (np.stack([v["x"], v["y"], v["z"]], -1).astype(np.float32),
            np.stack([v["r"], v["g"], v["b"]], -1).astype(np.uint8))


for key, c in CAMS.items():
    dp = "%s/%s_depth.npy" % (FRAMES, key)
    ply = "%s/%s.ply" % (FRAMES, key)
    if not (os.path.exists(dp) and os.path.exists(ply)):
        c["ok"] = False
        continue
    c["depth"] = np.load(dp)
    xyz, rgb = load_ply(ply)
    k = max(1, len(xyz) // 55000)
    xyz, rgb = xyz[::k], rgb[::k]
    P = np.stack([xyz[:, 0], -xyz[:, 2], xyz[:, 1]], -1)
    fz = np.percentile(P[:, 2], 2)
    P[:, 2] -= fz
    keep = P[:, 2] < CEIL * 1.3
    P, rgb = P[keep], rgb[keep]
    th = np.radians(c["yaw"])
    Rz = np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1]], np.float32)
    P = P @ Rz.T
    tx = bx0 + c["fx"] * (bx1 - bx0) - P[:, 0].mean()
    ty = by0 + c["fy"] * (by1 - by0) - P[:, 1].mean()
    tz = 0.0 if c["level"] == "level1" else F2F
    P += np.array([tx, ty, tz], np.float32)
    campos = np.array([0.0, 0.0, -fz], np.float32) @ Rz.T + np.array([tx, ty, tz], np.float32)
    c.update(ok=True, cloud=P.astype(np.float32), rgbc=rgb, Rz=Rz,
             t=np.array([tx, ty, tz], np.float32), floor=fz, campos=campos)
    print("%s placed, camera at (%.1f, %.1f, %.1f)" % (key, *campos), flush=True)

from ultralytics import YOLO
model = YOLO("yolo11m-seg.pt")

stop = False
for key, c in CAMS.items():
    if not c.get("ok"):
        continue
    c["frame"], c["lock"] = None, threading.Lock()

    def rd(cc=c):
        cap = cv2.VideoCapture(cc["url"], cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        while not stop:
            ok, fr = cap.read()
            if not ok:
                time.sleep(0.05); continue
            with cc["lock"]:
                cc["frame"] = fr
        cap.release()

    threading.Thread(target=rd, daemon=True).start()

WIN = "Lake Union Building - live 3D"
cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
cv2.resizeWindow(WIN, OW, OH)
cv2.setMouseCallback(WIN, on_mouse)
fps, t0 = 0.0, time.time()

while not stop:
    R, cam, f, T, rad = make_view()
    img = np.full((OH, OW, 3), 14, np.uint8)

    # ---- static clouds ----
    if view["clouds"] and not view["people_only"]:
        for key, c in CAMS.items():
            if not c.get("ok"):
                continue
            u, v, z, m1, m2 = proj(c["cloud"], R, cam, f, T)
            if len(u) == 0:
                continue
            col = c["rgbc"][m1][m2][:, ::-1]
            o = np.argsort(-z)
            splat(img, u[o], v[o], col[o], rad)

    # ---- walls ----
    for name, w in walls.items():
        z0 = 0.0 if name == "level1" else F2F
        wc = (150, 220, 255) if name == "level1" else (255, 205, 140)
        for x0, y0, x1, y1 in w[::2]:
            for zz in (z0, z0 + CEIL):
                line3(img, np.array([x0, y0, zz], np.float32),
                      np.array([x1, y1, zz], np.float32), R, cam, f, T, wc)

    # ---- camera frustums ----
    if view["frustums"]:
        halfh = np.radians(HFOV / 2)
        halfv = np.radians(HFOV / 2 * OH / OW)
        RANGE = 7.0
        for key, c in CAMS.items():
            if not c.get("ok"):
                continue
            o = c["campos"]
            corners = []
            for sx in (-1, 1):
                for sy in (-1, 1):
                    d = np.array([np.tan(halfh) * sx, 1.0, np.tan(halfv) * sy], np.float32)
                    d = d / np.linalg.norm(d)
                    corners.append(o + (d @ c["Rz"].T) * RANGE)
            for q in corners:
                line3(img, o, q, R, cam, f, T, c["col"], 1)
            for a, b in ((0, 1), (1, 3), (3, 2), (2, 0)):
                line3(img, corners[a], corners[b], R, cam, f, T, c["col"], 1)
            po = (o - T) @ R.T + cam
            if po[2] > 0.25:
                px = (int(f * po[0] / po[2] + OW / 2), int(OH / 2 - f * po[1] / po[2]))
                cv2.circle(img, px, 7, c["col"], -1)
                cv2.circle(img, px, 9, (255, 255, 255), 1)
                cv2.putText(img, c["label"], (px[0] + 12, px[1] - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, c["col"], 1, cv2.LINE_AA)

    # ---- live people ----
    total = 0
    for key, c in CAMS.items():
        if not c.get("ok"):
            continue
        with c["lock"]:
            fr = None if c["frame"] is None else c["frame"].copy()
        if fr is None:
            continue
        H, W = fr.shape[:2]
        res = model.predict(fr, imgsz=800, classes=[PERSON], conf=0.3,
                            device=0, verbose=False)[0]
        if res.masks is None:
            continue
        h, w = c["depth"].shape[0] // STRIDE, c["depth"].shape[1] // STRIDE
        fxs = w / (2.0 * np.tan(np.radians(HFOV) / 2.0))
        for mk, bx in zip(res.masks.data, res.boxes):
            m = mk.cpu().numpy()
            if m.shape != (H, W):
                m = cv2.resize(m, (W, H), interpolation=cv2.INTER_NEAREST)
            ys, xs = np.nonzero(m > 0.5)
            if len(xs) < 60:
                continue
            k = max(1, len(xs) // 1200)
            ys, xs = ys[::k], xs[::k]
            x1b, y1b, x2b, y2b = map(int, bx.xyxy[0])
            fv, fu = min(y2b, H - 1), (x1b + x2b) // 2
            dz = float(np.median(c["depth"][max(0, fv - 8):fv + 8, max(0, fu - 8):fu + 8]))
            if not np.isfinite(dz) or dz <= 0:
                continue
            X = (xs / STRIDE - w / 2.0) * dz / fxs
            Zc = -(ys / STRIDE - h / 2.0) * dz / fxs
            P = np.stack([X, np.full_like(X, dz), Zc], -1)
            P[:, 2] -= c["floor"]
            P = (P @ c["Rz"].T + c["t"]).astype(np.float32)
            u, v, z, m1, m2 = proj(P, R, cam, f, T)
            if len(u) == 0:
                continue
            splat(img, u, v, np.array([60, 255, 255], np.uint8), max(2, rad + 1))
            gx, gy = int(np.median(u)), int(np.max(v))
            cv2.circle(img, (gx, gy), 10, (0, 200, 255), 2)
            cv2.circle(img, (gx, gy), 3, (255, 255, 255), -1)
            total += 1

    dt = time.time() - t0; t0 = time.time()
    fps = 0.9 * fps + 0.1 * (1 / dt if dt > 0 else 0)
    cv2.rectangle(img, (0, 0), (OW, 62), (0, 0, 0), -1)
    cv2.putText(img, "1700 Westlake Ave N   people: %d   %.1f fps" % (total, fps),
                (14, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (245, 245, 245), 2)
    cv2.putText(img, "drag=orbit  wheel=zoom  f=frustums  c=clouds  p=people-only  r=reset   "
                     "dist %.0fm  splat %dpx" % (view["dist"], rad),
                (14, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (150, 150, 150), 1)

    cv2.imshow(WIN, img)
    k = cv2.waitKey(1) & 0xFF
    if k in (ord("q"), 27):
        break
    elif k == ord("r"):
        view.update(yaw=35.0, pitch=58.0, dist=SPAN * 1.05, target=HOME.copy())
    elif k == ord("f"):
        view["frustums"] = not view["frustums"]
    elif k == ord("c"):
        view["clouds"] = not view["clouds"]
    elif k == ord("p"):
        view["people_only"] = not view["people_only"]
    elif k in (ord("w"), ord("s"), ord("a"), ord("d")):
        step = view["dist"] * 0.08
        a = np.radians(view["yaw"])
        fwd = np.array([-np.sin(a), np.cos(a), 0], np.float32)
        rgt = np.array([np.cos(a), np.sin(a), 0], np.float32)
        view["target"] = view["target"] + (fwd * step if k == ord("w") else
                                           -fwd * step if k == ord("s") else
                                           -rgt * step if k == ord("a") else rgt * step)

stop = True
cv2.destroyAllWindows()
