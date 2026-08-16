"""Interactive real-time 3D room view.

  * static walls/floor/furniture : depth-estimated ONCE, cached, re-rasterised
    only when the viewpoint changes (drag the mouse)
  * moving people : segmented per frame, back-projected as real 3D points at
    their measured floor distance, so they move through the scene in 3D
  * left-drag = orbit,  scroll = dolly,  r = reset view,  q/esc = quit

  python live3d.py <room>
"""
import os, sys, time, threading
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
import numpy as np
import cv2

FRAMES = "/home/acer01/arlo-frames"
HFOV_DEG, OUT_W, OUT_H, STRIDE, PERSON = 120.0, 1280, 720, 3, 0
STREAMS = {
    "lobby":  os.environ.get("RTSP_LOBBY", ""),
    "common": os.environ.get("RTSP_COMMON", ""),
    "swhall": os.environ.get("RTSP_SWHALL", ""),
}
room = sys.argv[1] if len(sys.argv) > 1 else "lobby"
URL = STREAMS[room]

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


def project(pts, cols, fx, R, centroid, dolly):
    p = (pts - centroid) @ R + centroid
    p = p + np.array([0, 0, dolly], np.float32)
    Z = p[:, 2]
    m = Z < -0.2
    p, Z, cols = p[m], Z[m], cols[m]
    u = (fx * p[:, 0] / -Z + OUT_W / 2.0).astype(np.int32)
    v = (OUT_H / 2.0 - fx * p[:, 1] / -Z).astype(np.int32)
    m2 = (u >= 0) & (u < OUT_W) & (v >= 0) & (v < OUT_H)
    return u[m2], v[m2], Z[m2], cols[m2]


# ---------- one-time static cache ----------
print("[cache] reference frame ...", flush=True)
cap = cv2.VideoCapture(URL, cv2.CAP_FFMPEG)
ok, ref = cap.read(); cap.release()
if not ok:
    sys.exit("no reference frame")
H, W = ref.shape[:2]

dc = "%s/%s_depth.npy" % (FRAMES, room)
if os.path.exists(dc):
    depth = np.load(dc); print("[cache] depth reused", flush=True)
else:
    print("[cache] depth estimation (once) ...", flush=True)
    from transformers import pipeline
    from PIL import Image
    pipe = pipeline("depth-estimation",
                    model="depth-anything/Depth-Anything-V2-Metric-Indoor-Base-hf", device=0)
    d = np.array(pipe(Image.fromarray(cv2.cvtColor(ref, cv2.COLOR_BGR2RGB)))["predicted_depth"],
                 dtype=np.float32)
    depth = d[0] if d.ndim == 3 else d
    if depth.shape != (H, W):
        depth = cv2.resize(depth, (W, H), interpolation=cv2.INTER_LINEAR)
    np.save(dc, depth); print("[cache] depth saved", flush=True)

ds = depth[::STRIDE, ::STRIDE]
rgbs = cv2.cvtColor(ref, cv2.COLOR_BGR2RGB)[::STRIDE, ::STRIDE]
h, w = ds.shape
fx_src = w / (2.0 * np.tan(np.radians(HFOV_DEG) / 2.0))
uu, vv = np.meshgrid(np.arange(w), np.arange(h))
STATIC_P = np.stack([(uu - w / 2.0) * ds / fx_src,
                     -(vv - h / 2.0) * ds / fx_src, -ds], -1).reshape(-1, 3).astype(np.float32)
STATIC_C = rgbs.reshape(-1, 3).astype(np.uint8)
CENTROID = STATIC_P.mean(0)
FX_OUT = OUT_W / (2.0 * np.tan(np.radians(HFOV_DEG) / 2.0))
print("[cache] static cloud %d pts" % len(STATIC_P), flush=True)

from ultralytics import YOLO
model = YOLO("yolo11m-seg.pt")          # segmentation -> real person pixels

latest, lock, stop = None, threading.Lock(), False


def reader():
    global latest, stop
    c = cv2.VideoCapture(URL, cv2.CAP_FFMPEG)
    c.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    while not stop:
        okf, f = c.read()
        if not okf:
            time.sleep(0.05); continue
        with lock:
            latest = f
    c.release()


threading.Thread(target=reader, daemon=True).start()
win = "%s - live 3D  (drag=orbit  scroll=dolly  r=reset  q=quit)" % room
cv2.namedWindow(win, cv2.WINDOW_NORMAL)
cv2.resizeWindow(win, OUT_W, OUT_H)
cv2.setMouseCallback(win, on_mouse)

BG = None
fps, t0 = 0.0, time.time()

while not stop:
    with lock:
        frame = None if latest is None else latest.copy()
    if frame is None:
        time.sleep(0.05); continue

    R = view_R(view["yaw"], view["pitch"])

    # --- cached static geometry: rebuilt only when the view changed ---
    if view["dirty"] or BG is None:
        su, sv, sz, sc = project(STATIC_P, STATIC_C, FX_OUT, R, CENTROID, view["dolly"])
        BG = np.zeros((OUT_H, OUT_W, 3), np.uint8)
        o = np.argsort(sz)
        BG[sv[o], su[o]] = sc[o]
        BG = cv2.medianBlur(BG, 3)
        view["dirty"] = False
    canvas = BG.copy()

    # --- moving objects: segmented, back-projected, drawn as 3D points ---
    res = model.predict(frame, imgsz=960, classes=[PERSON], conf=0.3,
                        device=0, verbose=False, retina_masks=False)[0]
    people = 0
    if res.masks is not None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        for mk, bx in zip(res.masks.data, res.boxes):
            m = mk.cpu().numpy()
            if m.shape != (H, W):
                m = cv2.resize(m, (W, H), interpolation=cv2.INTER_NEAREST)
            ys, xs = np.nonzero(m > 0.5)
            if len(xs) < 60:
                continue
            k = max(1, len(xs) // 4000)                  # cap points per person
            ys, xs = ys[::k], xs[::k]
            x1, y1, x2, y2 = map(int, bx.xyxy[0])
            fv = min(y2, H - 1); fu = (x1 + x2) // 2
            dz = float(np.median(depth[max(0, fv - 8):fv + 8, max(0, fu - 8):fu + 8]))
            if not np.isfinite(dz) or dz <= 0:
                continue
            # place the whole silhouette at the person's floor distance
            X = (xs / STRIDE - w / 2.0) * dz / fx_src
            Y = -(ys / STRIDE - h / 2.0) * dz / fx_src
            pts = np.stack([X, Y, np.full_like(X, -dz)], -1).astype(np.float32)
            cols = rgb[ys, xs].astype(np.uint8)
            pu, pv, pz, pc = project(pts, cols, FX_OUT, R, CENTROID, view["dolly"])
            if len(pu) == 0:
                continue
            canvas[pv, pu] = pc
            cv2.circle(canvas, (int(np.median(pu)), int(pv.max())), 6, (0, 220, 255), -1)
            cv2.putText(canvas, "%.1fm" % dz, (int(np.median(pu)) + 8, int(pv.min())),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            people += 1

    dt = time.time() - t0; t0 = time.time()
    fps = 0.9 * fps + 0.1 * (1.0 / dt if dt > 0 else 0)
    cv2.rectangle(canvas, (0, 0), (700, 42), (0, 0, 0), -1)
    cv2.putText(canvas, "%s | people %d | %.1f fps | yaw %.0f pitch %.0f" %
                (room, people, fps, view["yaw"], view["pitch"]),
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    cv2.imshow(win, canvas)
    k = cv2.waitKey(1) & 0xFF
    if k in (ord("q"), 27):
        break
    if k == ord("r"):
        view.update(yaw=15.0, pitch=-10.0, dolly=0.0, dirty=True)

stop = True
cv2.destroyAllWindows()
