"""All rooms, live, in 3D, under one mouse-driven camera.

Each room keeps its own cached static point cloud (depth estimated once).
The shared view state drives every tile, so dragging orbits all rooms together.
Only segmented people are recomputed per frame.

  drag = orbit all   scroll = dolly   r = reset   q/esc = quit
"""
import os, sys, time, threading
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import numpy as np
import cv2

FRAMES = "/home/acer01/arlo-frames"
HFOV_DEG, TW, TH, STRIDE, PERSON = 120.0, 640, 540, 3, 0
ROOMS = [
    ("lobby",  os.environ.get("RTSP_LOBBY", ""), "2F LOBBY"),
    ("common", os.environ.get("RTSP_COMMON", ""), "1F COMMON AREA"),
    ("swhall", os.environ.get("RTSP_SWHALL", ""), "2F SW HALLWAY"),
]

view = {"yaw": 15.0, "pitch": -10.0, "dolly": 0.0, "drag": False, "x": 0, "y": 0, "dirty": True}


def on_mouse(event, x, y, flags, _):
    if event == cv2.EVENT_LBUTTONDOWN:
        view.update(drag=True, x=x, y=y)
    elif event == cv2.EVENT_LBUTTONUP:
        view["drag"] = False
    elif event == cv2.EVENT_MOUSEMOVE and view["drag"]:
        view["yaw"] += (x - view["x"]) * 0.35
        view["pitch"] = max(-80, min(80, view["pitch"] + (y - view["y"]) * 0.30))
        view.update(x=x, y=y, dirty=True)
    elif event == cv2.EVENT_MOUSEWHEEL:
        view["dolly"] += 0.5 if flags > 0 else -0.5
        view["dirty"] = True


def view_R(yaw, pitch):
    ry, rx = np.radians(yaw), np.radians(pitch)
    Ry = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]], np.float32)
    Rx = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]], np.float32)
    return (Ry @ Rx).T


FX = TW / (2.0 * np.tan(np.radians(HFOV_DEG) / 2.0))


def project(pts, cols, R, centroid, dolly):
    p = (pts - centroid) @ R + centroid + np.array([0, 0, dolly], np.float32)
    Z = p[:, 2]
    m = Z < -0.2
    p, Z, cols = p[m], Z[m], cols[m]
    u = (FX * p[:, 0] / -Z + TW / 2.0).astype(np.int32)
    v = (TH / 2.0 - FX * p[:, 1] / -Z).astype(np.int32)
    m2 = (u >= 0) & (u < TW) & (v >= 0) & (v < TH)
    return u[m2], v[m2], Z[m2], cols[m2]


# ---------- build static cache for every room ----------
from transformers import pipeline as hf_pipeline
from PIL import Image

dpipe = None
rooms = []
for key, url, label in ROOMS:
    print("[%s] reference frame ..." % key, flush=True)
    c = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    ok, ref = c.read(); c.release()
    if not ok:
        print("[%s] SKIPPED - no frame" % key, flush=True); continue
    H, W = ref.shape[:2]
    dc = "%s/%s_depth.npy" % (FRAMES, key)
    if os.path.exists(dc):
        depth = np.load(dc)
    else:
        if dpipe is None:
            dpipe = hf_pipeline("depth-estimation",
                                model="depth-anything/Depth-Anything-V2-Metric-Indoor-Base-hf",
                                device=0)
        d = np.array(dpipe(Image.fromarray(cv2.cvtColor(ref, cv2.COLOR_BGR2RGB)))["predicted_depth"],
                     dtype=np.float32)
        depth = d[0] if d.ndim == 3 else d
        if depth.shape != (H, W):
            depth = cv2.resize(depth, (W, H), interpolation=cv2.INTER_LINEAR)
        np.save(dc, depth)
    ds = depth[::STRIDE, ::STRIDE]
    rgbs = cv2.cvtColor(ref, cv2.COLOR_BGR2RGB)[::STRIDE, ::STRIDE]
    h, w = ds.shape
    fx_src = w / (2.0 * np.tan(np.radians(HFOV_DEG) / 2.0))
    uu, vv = np.meshgrid(np.arange(w), np.arange(h))
    P = np.stack([(uu - w / 2.0) * ds / fx_src,
                  -(vv - h / 2.0) * ds / fx_src, -ds], -1).reshape(-1, 3).astype(np.float32)
    rooms.append({
        "key": key, "url": url, "label": label, "H": H, "W": W,
        "depth": depth, "P": P, "C": rgbs.reshape(-1, 3).astype(np.uint8),
        "centroid": P.mean(0), "fx_src": fx_src, "h": h, "w": w,
        "bg": None, "frame": None, "lock": threading.Lock(),
    })
    print("[%s] cached %d pts" % (key, len(P)), flush=True)

if not rooms:
    sys.exit("no rooms available")

from ultralytics import YOLO
model = YOLO("yolo11m-seg.pt")

stop = False


def reader(r):
    c = cv2.VideoCapture(r["url"], cv2.CAP_FFMPEG)
    c.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    while not stop:
        ok, f = c.read()
        if not ok:
            time.sleep(0.05); continue
        with r["lock"]:
            r["frame"] = f
    c.release()


for r in rooms:
    threading.Thread(target=reader, args=(r,), daemon=True).start()

win = "Thinkspace live 3D - all rooms  (drag=orbit  scroll=dolly  r=reset  q=quit)"
cv2.namedWindow(win, cv2.WINDOW_NORMAL)
cv2.resizeWindow(win, min(1900, TW * len(rooms)), TH)
cv2.setMouseCallback(win, on_mouse)
fps, t0 = 0.0, time.time()

while not stop:
    R = view_R(view["yaw"], view["pitch"])
    rebuild = view["dirty"]
    tiles = []

    for r in rooms:
        if rebuild or r["bg"] is None:
            u, v, z, c = project(r["P"], r["C"], R, r["centroid"], view["dolly"])
            bg = np.zeros((TH, TW, 3), np.uint8)
            o = np.argsort(z)
            bg[v[o], u[o]] = c[o]
            r["bg"] = cv2.medianBlur(bg, 3)
        canvas = r["bg"].copy()

        with r["lock"]:
            frame = None if r["frame"] is None else r["frame"].copy()

        people = 0
        if frame is not None:
            res = model.predict(frame, imgsz=800, classes=[PERSON], conf=0.3,
                                device=0, verbose=False)[0]
            if res.masks is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                H, W, h, w, fx_src = r["H"], r["W"], r["h"], r["w"], r["fx_src"]
                for mk, bx in zip(res.masks.data, res.boxes):
                    m = mk.cpu().numpy()
                    if m.shape != (H, W):
                        m = cv2.resize(m, (W, H), interpolation=cv2.INTER_NEAREST)
                    ys, xs = np.nonzero(m > 0.5)
                    if len(xs) < 60:
                        continue
                    k = max(1, len(xs) // 2500)
                    ys, xs = ys[::k], xs[::k]
                    x1, y1, x2, y2 = map(int, bx.xyxy[0])
                    fv, fu = min(y2, H - 1), (x1 + x2) // 2
                    dz = float(np.median(r["depth"][max(0, fv - 8):fv + 8, max(0, fu - 8):fu + 8]))
                    if not np.isfinite(dz) or dz <= 0:
                        continue
                    X = (xs / STRIDE - w / 2.0) * dz / fx_src
                    Y = -(ys / STRIDE - h / 2.0) * dz / fx_src
                    pts = np.stack([X, Y, np.full_like(X, -dz)], -1).astype(np.float32)
                    pu, pv, pz, pc = project(pts, rgb[ys, xs].astype(np.uint8),
                                             R, r["centroid"], view["dolly"])
                    if len(pu):
                        canvas[pv, pu] = pc
                        cv2.putText(canvas, "%.1fm" % dz,
                                    (int(np.median(pu)) + 6, int(pv.min())),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                        people += 1

        cv2.rectangle(canvas, (0, 0), (TW, 34), (0, 0, 0), -1)
        cv2.putText(canvas, "%s   people %d" % (r["label"], people),
                    (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
        cv2.rectangle(canvas, (0, 0), (TW - 1, TH - 1), (60, 60, 60), 1)
        tiles.append(canvas)

    view["dirty"] = False
    grid = np.hstack(tiles)

    dt = time.time() - t0; t0 = time.time()
    fps = 0.9 * fps + 0.1 * (1.0 / dt if dt > 0 else 0)
    cv2.rectangle(grid, (0, TH - 30), (520, TH), (0, 0, 0), -1)
    cv2.putText(grid, "%.1f fps | yaw %.0f pitch %.0f | static cached" %
                (fps, view["yaw"], view["pitch"]), (10, TH - 9),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    cv2.imshow(win, grid)
    k = cv2.waitKey(1) & 0xFF
    if k in (ord("q"), 27):
        break
    if k == ord("r"):
        view.update(yaw=15.0, pitch=-10.0, dolly=0.0, dirty=True)

stop = True
cv2.destroyAllWindows()
