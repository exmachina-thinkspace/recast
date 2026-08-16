"""Phone camera bridge: scan a QR, grant camera, stream into the Spark app.

No app install, no WebRTC signalling server. The phone page grabs frames with
getUserMedia, draws to a canvas, and POSTs JPEGs. The Spark keeps only the
newest frame per device, so latency stays flat and memory stays bounded —
which matters, this box runs ~95 GB of resident inference.

  python phone_bridge.py [port]        default 8099
"""
import io, os, sys, ssl, time, json, socket, threading, subprocess, collections
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import numpy as np
import cv2

import phone_slam
import plan_render
import render_room_depth
import scenegraph3d as sg3d
import interior_gen
try:
    import scan_match
    HAVE_SCAN_MATCH = True
except Exception as _e:
    scan_match = None
    HAVE_SCAN_MATCH = False
    print("[bridge] scan_match unavailable (%s) -> falling back to phone_slam.place" % str(_e)[:120], flush=True)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
DEVICES = {}          # id -> {"jpeg": bytes, "ts": float, "name": str, "n": int}
SENSORS = {}          # id -> latest orientation/motion/geo sample (flat snapshot,
                       # unchanged shape from before -- consumers like
                       # localize_snapshot() below keep working untouched)
CAPS = {}              # id -> per-device capability summary (see /capabilities/<id>)
MOTION_BUF_MAX = 600   # ~ a few seconds of high-rate accel/gyro samples per device
MOTION_BUF = {}        # id -> deque of raw devicemotion samples (for pdr.py, future)
ORIENT_BUF = {}        # id -> deque of raw deviceorientation(absolute) samples
ERRORS = []           # every client-side failure, newest last (capped)
MAX_ERRORS = 300
LOCK = threading.Lock()
STALE_S = 12.0

# ---------------- SNAPSHOT mode: localize a phone viewpoint + generate a
# repurposed-interior render from it. Reuses the same plan geometry / pose
# math the live desktop app (spark_app.py, not touched here) uses.
PLANS = os.path.expanduser("~/plans")
CEIL = render_room_depth.CEIL
F2F = render_room_depth.F2F
EYE = render_room_depth.EYE
VIEWPOINTS = {}        # dev id -> last localized pose dict
LAST_FRAME = {}   # dev -> path of the most recent capture
JOBS = {}              # dev id -> generate job status dict
_DEPTH_MODEL = None
GEN_LOCK = threading.Lock()
SNAP_LOCK = threading.Lock()


def _load_levels():
    """Plan geometry per storey, preferring datum-corrected *_aligned* files."""
    lv_out = {}
    for lv, base_z in (("level1", 0.0), ("level2", F2F)):
        wp = "%s/%s_walls_m_aligned.npy" % (PLANS, lv)
        if not os.path.exists(wp):
            wp = "%s/%s_walls_m.npy" % (PLANS, lv)
        rp = "%s/%s_rooms_v2_aligned.json" % (PLANS, lv)
        for alt in ("%s/%s_rooms_v2.json" % (PLANS, lv), "%s/%s_rooms.json" % (PLANS, lv)):
            if os.path.exists(rp):
                break
            rp = alt
        if not os.path.exists(wp):
            continue
        lv_out[lv] = dict(walls=np.load(wp), base_z=base_z,
                          rooms=json.load(open(rp)) if os.path.exists(rp) else [])
        print("[bridge] snapshot geometry %s: %d walls, %d rooms (%s)" %
              (lv, len(lv_out[lv]["walls"]), len(lv_out[lv]["rooms"]), os.path.basename(rp)), flush=True)
    return lv_out


LEVELS = _load_levels()


def free_gib():
    try:
        o = subprocess.run(["free", "-g"], capture_output=True, text=True, timeout=5).stdout
        return int([l for l in o.splitlines() if l.startswith("Mem:")][0].split()[6])
    except Exception:
        return 0


def _get_depth_model():
    """Lazy-load the small YOLO26 depth model (same one spark_app.py uses)."""
    global _DEPTH_MODEL
    if _DEPTH_MODEL is not None:
        return _DEPTH_MODEL
    from ultralytics import YOLO
    for cand in ("yolo26s-depth.pt", "yolo26n-depth.pt"):
        p = os.path.expanduser("~/arlo-vision/%s" % cand)
        if os.path.exists(p):
            _DEPTH_MODEL = YOLO(p)
            print("[bridge] snapshot depth model: %s" % cand, flush=True)
            break
    return _DEPTH_MODEL


def _infer_depth(img):
    m = _get_depth_model()
    if m is None:
        return None
    try:
        r = m.predict(img, verbose=False)[0]
        for attr in ("depth", "depths"):
            o = getattr(r, attr, None)
            if o is None:
                continue
            d = o.data.cpu().numpy() if hasattr(o, "data") else np.asarray(o)
            d = np.squeeze(d).astype(np.float32)
            if d.ndim == 2:
                return d
    except Exception as e:
        print("[snapshot] depth inference failed: %s" % str(e)[:120], flush=True)
    return None


def _backproject(img, d, stride=4):
    """Metric depth -> point cloud in device axes (X right, Y up, Z toward user).
    Same convention/formula as spark_app.py's backproject(), so it stays
    compatible with phone_slam / scan_match, which were built against it."""
    Hh, Ww = img.shape[:2]
    if d.shape != (Hh, Ww):
        d = cv2.resize(d, (Ww, Hh), interpolation=cv2.INTER_LINEAR)
    ds = d[::stride, ::stride]
    h, w = ds.shape
    fx = w / (2.0 * np.tan(np.radians(68.0) / 2.0))     # typical phone hFOV
    uu, vv = np.meshgrid(np.arange(w), np.arange(h))
    P = np.stack([(uu - w / 2.0) * ds / fx, -(vv - h / 2.0) * ds / fx, -ds],
                 -1).reshape(-1, 3).astype(np.float32)
    ok = np.isfinite(P).all(1) & (ds.reshape(-1) > 0.15) & (ds.reshape(-1) < 25.0)
    return P[ok]


def _yaw2(deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s], [s, c]], np.float64)


def _find_room(lv, xy):
    for i, r in enumerate(LEVELS.get(lv, {}).get("rooms", [])):
        if sg3d.point_in_poly(xy, r["poly"]):
            return "%s_room_%02d" % (lv, i)
    return None


def localize_snapshot(dev_id, img):
    """JPEG frame (decoded) + this device's latest sensor sample -> pose on
    the floor plan: {x, y, level, heading_deg, room_id, confidence, method}.
    Uses scan_match.localize() (wall-band vs. plan distance-transform) when
    available; falls back to phone_slam.place() (area/aspect room match)."""
    t0 = time.time()
    d = _infer_depth(img)
    if d is None:
        # GPU is busy (the desktop app holds its own depth model) or inference
        # failed. Localization is unavailable, but the CAPTURE still succeeded
        # and generation runs from the photo — so report degraded, not failed.
        return {"degraded": "no depth available (GPU busy); capture kept, "
                            "generation still works", "pose": None}
    P = _backproject(img, d)
    if len(P) < 300:
        return {"error": "too few depth points (%d) to localize" % len(P)}

    with LOCK:
        srec = dict(SENSORS.get(dev_id, {}))
    gravity = None
    if srec.get("gx") is not None and srec.get("gy") is not None and srec.get("gz") is not None:
        gravity = [srec["gx"], srec["gy"], srec["gz"]]
    heading = srec.get("heading")

    up, Rg = phone_slam.gravity_align(P, gravity)
    lev, _floor = phone_slam.refine_floor(up)

    pose = None
    if HAVE_SCAN_MATCH:
        best = None
        for lv, L in LEVELS.items():
            try:
                res = scan_match.localize(lev, L["walls"], base_z=L["base_z"],
                                          theta_step=6.0, res=0.10, max_points=250)
            except Exception as e:
                print("[snapshot] scan_match failed on %s: %s" % (lv, str(e)[:120]), flush=True)
                res = None
            if res and res.get("x") is not None:
                cand = dict(level=lv, x=res["x"], y=res["y"], theta=res["theta_deg"],
                            confidence=res["confidence"], base_z=L["base_z"])
                if best is None or cand["confidence"] > best["confidence"]:
                    best = cand
        if best is not None:
            fwd_up = np.array([0.0, 0.0, -1.0]) @ Rg.T
            fwd_world = fwd_up[:2] @ _yaw2(best["theta"]).T
            heading_deg = float(np.degrees(np.arctan2(fwd_world[1], fwd_world[0])) % 360.0)
            room_id = _find_room(best["level"], (best["x"], best["y"]))
            pose = dict(x=best["x"], y=best["y"], level=best["level"], base_z=best["base_z"],
                       heading_deg=heading_deg, room_id=room_id,
                       confidence=best["confidence"], method="scan_match")

    if pose is None:
        # fallback: phone_slam's area/aspect room match, tried on both storeys
        best2 = None
        for lv, L in LEVELS.items():
            r = phone_slam.place(P, np.zeros((len(P), 3), np.uint8), L["base_z"],
                                 L["walls"], L["rooms"], gravity=gravity, compass_deg=heading)
            if r is None:
                continue
            r["level"] = lv
            score = abs(np.log(max(r["footprint_area"], .1) / max(r["room_area"], .1))) \
                if r["room_area"] else 1e9
            if best2 is None or score < best2[0]:
                best2 = (score, r)
        if best2 is None or best2[1]["confidence"] == "unplaced":
            return {"error": "localization failed (unplaced on both storeys)"}
        r = best2[1]
        xf = r["transform"]
        eye_w = phone_slam.apply_transform(np.array([[0.0, 0.0, 0.0]], np.float32), xf)[0]
        fwd_w = phone_slam.apply_transform(np.array([[0.0, 0.0, -1.0]], np.float32), xf)[0]
        hv = fwd_w[:2] - eye_w[:2]
        heading_deg = float(np.degrees(np.arctan2(hv[1], hv[0])) % 360.0)
        pose = dict(x=float(eye_w[0]), y=float(eye_w[1]), level=r["level"],
                   base_z=LEVELS[r["level"]]["base_z"], heading_deg=heading_deg,
                   room_id=r["room_id"], confidence=r["confidence"], method="phone_slam")

    pose["device"] = dev_id
    pose["ts"] = time.time()
    pose["elapsed_s"] = round(time.time() - t0, 2)
    return pose


def render_pose_depth(pose, out_npy, w=512, h=512, hfov=75.0):
    """Render plan walls + scenegraph objects from an ARBITRARY pose (this
    phone's localized viewpoint) into a metric depth map, via
    render_room_depth's zbuffer/look_at (reused, not its default room camera)."""
    lv = pose["level"]
    L = LEVELS[lv]
    base_z = L["base_z"]
    eye_xy = np.array([pose["x"], pose["y"]], np.float64)
    room_id = pose.get("room_id")
    poly = None
    if room_id:
        idx = int(room_id.rsplit("_", 1)[1])
        if idx < len(L["rooms"]):
            poly = L["rooms"][idx]["poly"]

    pad = 1.5
    if poly is not None:
        Pp = np.asarray(poly, np.float64)
        x0, x1 = Pp[:, 0].min() - pad, Pp[:, 0].max() + pad
        y0, y1 = Pp[:, 1].min() - pad, Pp[:, 1].max() + pad
    else:
        rad = 6.0
        x0, x1 = eye_xy[0] - rad, eye_xy[0] + rad
        y0, y1 = eye_xy[1] - rad, eye_xy[1] + rad

    walls = L["walls"]
    mx = ((walls[:, [0, 2]] >= x0) & (walls[:, [0, 2]] <= x1)).any(1)
    my = ((walls[:, [1, 3]] >= y0) & (walls[:, [1, 3]] <= y1)).any(1)
    near = walls[mx & my]
    Q = [plan_render.wall_quads(near, base_z, CEIL, stride=1)]
    for z in (base_z, base_z + CEIL):
        Q.append(np.array([[[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]]], np.float32))

    nobj = 0
    sgp = os.path.join(PLANS, "scenegraph.json")
    if room_id and os.path.exists(sgp):
        try:
            sgd = json.load(open(sgp))
            for lvd in sgd.get("levels", []):
                for r in lvd.get("rooms", []):
                    if r.get("room_id", r.get("id")) != room_id:
                        continue
                    for o in r.get("objects", []):
                        size = render_room_depth.OBJ_SIZE.get(o.get("cls"), (0.4, 0.4, 0.4))
                        Q.append(plan_render.box_quads(o["position"], size))
                        nobj += 1
        except Exception as e:
            print("[snapshot] scenegraph read failed: %s" % str(e)[:100], flush=True)

    quads = np.concatenate(Q)
    eye = np.array([eye_xy[0], eye_xy[1], base_z + EYE], np.float32)
    hd = np.radians(pose["heading_deg"])
    fwd = np.array([np.cos(hd), np.sin(hd)])
    tgt_xy = eye_xy + fwd * 3.0
    tgt = np.array([tgt_xy[0], tgt_xy[1], base_z + EYE * 0.9], np.float32)
    R = render_room_depth.look_at(eye, tgt)
    Z = render_room_depth.zbuffer(quads, R, eye, hfov, w, h)
    hit = float(np.isfinite(Z).mean())
    Zs = Z.copy()
    Zs[~np.isfinite(Zs)] = np.nan
    np.save(out_npy, Zs.astype(np.float32))
    try:
        cv2.imwrite(out_npy.replace(".npy", "_preview.png"), render_room_depth.depth_to_png(Z))
    except Exception:
        pass
    return dict(coverage=round(hit, 3), n_objects=nobj, n_walls=int(len(near)))


def record_snapshot(dev_id, pose, preset, out_png):
    """Append this snapshot+render to ~/plans/snapshots.json so the desktop
    app can show where phones stood, without touching spark_app.py itself."""
    path = os.path.join(PLANS, "snapshots.json")
    with SNAP_LOCK:
        try:
            data = json.load(open(path)) if os.path.exists(path) else []
        except Exception:
            data = []
        data.append(dict(
            device=dev_id, x=round(pose["x"], 2), y=round(pose["y"], 2),
            level=pose["level"], heading_deg=round(pose["heading_deg"], 1),
            room_id=pose.get("room_id"), confidence=pose.get("confidence"),
            method=pose.get("method"), preset=preset, image=out_png,
            ts=time.time(), generated_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
        tmp = path + ".tmp"
        json.dump(data, open(tmp, "w"), indent=1)
        os.replace(tmp, path)


def run_generate(dev_id, preset):
    JOBS[dev_id] = dict(status="running", preset=preset, started=time.time())
    try:
        pose = VIEWPOINTS.get(dev_id)
        if not pose:
            fp = LAST_FRAME.get(dev_id)
            if fp and os.path.exists(fp):
                # No pose, but we have the picture — generate from it directly.
                # A localized render is better; refusing to produce anything is
                # worse than producing an unlocalized one.
                try:
                    out_png = os.path.join(PLANS, "snap_gen_%s.png" % dev_id)
                    t0 = time.time()
                    interior_gen.generate(fp, preset, out_png)
                    JOBS[dev_id] = dict(status="done", render_s=0.0,
                                        gen_s=round(time.time() - t0, 1),
                                        total_s=round(time.time() - t0, 1),
                                        source="frame", png=out_png)
                except Exception as e:
                    JOBS[dev_id] = dict(status="error", error=str(e)[:200])
                return
            JOBS[dev_id] = dict(status="error", error="no capture yet - tap the shutter first")
            return
        if preset not in interior_gen.PRESETS:
            JOBS[dev_id] = dict(status="error", error="unknown preset %r" % preset)
            return
        with GEN_LOCK:                      # one generation at a time on this box
            fg = free_gib()
            if fg < 8:
                JOBS[dev_id] = dict(status="error",
                    error="only %dGiB free RAM (<8GiB) - refusing to load the image generator" % fg)
                return
            depth_npy = os.path.join(PLANS, "snap_depth_%s.npy" % dev_id)
            t_r0 = time.time()
            meta = render_pose_depth(pose, depth_npy)
            t_r1 = time.time()
            out_png = os.path.join(PLANS, "snap_gen_%s.png" % dev_id)
            t_g0 = time.time()
            # The captured PHOTO is the structural source, not the synthetic
            # depth render. Rendering the plan from a virtual camera produced a
            # near-flat surface (n_objects=0) and SD faithfully generated a bare
            # wood floor. The frame the user actually shot has the real room in
            # it, which is the whole point of "turn this view into that use".
            _src = LAST_FRAME.get(dev_id)
            interior_gen.generate(_src if (_src and os.path.exists(_src)) else depth_npy,
                                  preset, out_png)
            t_g1 = time.time()
        record_snapshot(dev_id, pose, preset, out_png)
        JOBS[dev_id] = dict(status="done", preset=preset, image="/genimg/%s" % dev_id,
                            render_s=round(t_r1 - t_r0, 2), gen_s=round(t_g1 - t_g0, 2),
                            total_s=round(t_g1 - t_r0, 2), meta=meta, finished=time.time())
    except Exception as e:
        JOBS[dev_id] = dict(status="error", error=str(e)[:300])
        print("[generate] failed: %s" % str(e)[:200], flush=True)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


HOST_IP = os.environ.get("BRIDGE_HOST", lan_ip())
CERT = os.path.expanduser("~/plans/bridge_cert.pem")
KEY = os.path.expanduser("~/plans/bridge_key.pem")


def ensure_cert():
    """Browsers block getUserMedia outside a secure context, so plain HTTP on a
    LAN IP gets 'camera denied' regardless of what the user taps. Self-signed
    TLS is the smallest thing that makes the camera prompt appear at all."""
    if os.path.exists(CERT) and os.path.exists(KEY):
        return True
    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", KEY, "-out", CERT, "-days", "365",
            "-subj", "/CN=%s" % HOST_IP,
            "-addext", "subjectAltName=IP:%s,IP:127.0.0.1" % HOST_IP,
        ], check=True, capture_output=True, timeout=60)
        return True
    except Exception as e:
        print("[bridge] cert generation failed: %s" % str(e)[:120], flush=True)
        return False


USE_TLS = ensure_cert()
SCHEME = "https" if USE_TLS else "http"
URL = "%s://%s:%d/" % (SCHEME, HOST_IP, PORT)

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Recast — share camera</title>
<style>
 body{margin:0;background:#111;color:#eee;font:16px -apple-system,system-ui,sans-serif;
      display:flex;flex-direction:column;align-items:center;gap:14px;padding:18px}
 video{width:100%;max-width:480px;border-radius:12px;background:#000}
 button{font-size:17px;padding:14px 26px;border-radius:10px;border:0;
        background:#2d7;color:#000;font-weight:600}
 button:disabled{background:#444;color:#888}
 #s{font-size:14px;color:#8b8}
 #s2{font-size:14px;color:#9cf;min-height:18px}
 .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;justify-content:center}
 input{font-size:15px;padding:9px;border-radius:8px;border:1px solid #444;
       background:#1c1c1c;color:#eee;width:150px}
 select{font-size:15px;padding:10px;border-radius:8px;border:1px solid #444;
        background:#1c1c1c;color:#eee}
 .modebtn{background:#333;color:#ccc}
 .modebtn.on{background:#2d7;color:#000}
 #genimg{width:100%;max-width:480px;border-radius:12px;display:none;background:#000}

#capBtn{width:78px;height:78px;border-radius:50%;border:5px solid #fff;
  background:#e33;box-shadow:0 0 0 3px rgba(0,0,0,.35);margin:10px auto;display:block;
  cursor:pointer;transition:transform .08s,background .2s;padding:0}
#capBtn:active{transform:scale(.92)}
#capBtn:disabled{background:#666;border-color:#999}
#capBtn.busy{background:#c80;animation:pulse 1s infinite}
@keyframes pulse{0%{opacity:1}50%{opacity:.55}100%{opacity:1}}
#shutterWrap{text-align:center;margin:6px 0}
#shutterHint{font-size:12px;color:#9ab;margin-top:2px}

#sensWarn{display:none;width:100%;border:0;cursor:pointer;background:#b3261e;color:#fff;font-weight:600;
  padding:12px;border-radius:10px;margin:8px 0;text-align:center;font-size:15px;
  animation:sensPulse 1.6s infinite}
@keyframes sensPulse{0%{opacity:1}50%{opacity:.72}100%{opacity:1}}

  50%{transform:scale(1.06);opacity:.75}100%{transform:scale(1);opacity:1}}

#permHelp{display:none;position:fixed;left:0;right:0;bottom:0;z-index:9998;
  background:#7f1d1d;color:#fff;padding:14px 16px;font-size:14px;line-height:1.5;
  box-shadow:0 -4px 18px rgba(0,0,0,.5)}
#permHelp b{display:block;font-size:15px;margin-bottom:6px}
#permHelp ol{margin:6px 0 8px 18px;padding:0}
#permHelp li{margin:3px 0}
#permHelp button{background:#fff;color:#7f1d1d;border:0;border-radius:8px;
  padding:9px 14px;font-weight:700;font-size:14px;margin-top:4px}

html,body{height:100%;margin:0;padding:0;background:#000;overflow:hidden}
h3{display:none}                       /* the feed is the page, not a heading */
#v{position:fixed;left:0;top:0;width:100vw;height:100vh;object-fit:cover;
   background:#000;z-index:0}
#s{position:fixed;left:0;right:0;top:0;z-index:5;margin:0;padding:6px 10px;
   font-size:12px;color:#cde;background:rgba(0,0,0,.45);text-align:center}
#sensStat{position:fixed;left:0;right:0;top:26px;z-index:5;text-align:center;
   font-size:12px;color:#9cf;background:rgba(0,0,0,.35)}
#who{display:none}
#snapRow{position:fixed;left:0;right:0;bottom:22px;z-index:6;display:flex;
   align-items:center;justify-content:center;gap:14px;margin:0}
#genimg{position:fixed;right:10px;top:56px;width:34vw;max-width:190px;z-index:6;
   border-radius:10px;border:2px solid rgba(120,255,190,.85)}
#preset{max-width:46vw}
/* overlays must never push the video: fixed, compact, dismissable */
#permHelp{position:fixed;left:8px;right:8px;bottom:96px;z-index:8;
   max-height:38vh;overflow:auto;border-radius:12px}
#sensWarn{position:fixed;left:50%;transform:translateX(-50%);top:54px;
   z-index:9;width:auto;max-width:88vw;border-radius:22px;padding:11px 18px;
   background:#b3261e;color:#fff;border:0;font-weight:700;font-size:14px;
   box-shadow:0 3px 14px rgba(0,0,0,.5);display:none}
</style></head><body>
<h3>Share this camera with Recast</h3>
<div class=row><span id=who></span></div>
<div class=row><button id=go style="display:none">Start camera</button>
 <button id=sensBtn style="display:none">Enable Motion &amp; Sensors</button></div>
<div id=sensStat style="font-size:13px;color:#9cf;min-height:16px"></div>
<input id=nm type=hidden>
<div class=row>
 </div>
<video id=v autoplay playsinline muted></video>
<div id=s>idle</div>
<div id=permHelp><b>Motion sensors are blocked - position cannot be tracked</b><span id=permWhy></span><ol><li>Tap <b>aA</b> in the address bar (top left)</li><li>Choose <b>Website Settings</b></li><li>Turn on <b>Motion &amp; Orientation Access</b>, then tap Retry</li></ol><div style='font-size:12px;opacity:.85;margin-top:4px'>Still blocked? Settings &rarr; Apps &rarr; Safari &rarr; Motion &amp; Orientation Access</div><button id=permRetry type=button>Retry now</button></div>
<button id=sensWarn type=button>TAP TO ENABLE MOTION</button>
<div class=row id=snapRow style="display:flex">
 <button id=capBtn>Take Picture</button>
 <select id=preset>
  <option value="dentist office">Dentist office</option>
  <option value="condo">Condo</option>
  <option value="garage">Garage</option>
  <option value="coworking space">Coworking space</option>
  <option value="cafe">Cafe</option>
  <option value="retail">Retail</option>
 </select>
 <button id=genBtn style='display:none' disabled>Generate</button>
</div>
<div id=s2></div>
<img id=genimg>
<script>
let id = localStorage.getItem('rcid') || (Math.random().toString(36).slice(2,9));
localStorage.setItem('rcid', id);
function rlog(kind, detail){
  try{ fetch('/log/'+id, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({kind:kind, detail:String(detail).slice(0,400),
      name: (document.getElementById('nm')||{}).value || 'phone',
      ua: navigator.userAgent.slice(0,160),
      secure: window.isSecureContext, proto: location.protocol,
      host: location.host, t: Date.now()})}); }catch(e){}
}
// --- device sensors ------------------------------------------------------
// heading orients the camera in the building; gravity gives tilt; high-rate
// accel+gyro feed pdr.py (pedestrian dead reckoning) for when vision alone
// is ambiguous (repeating structural bays -> corridor aliasing). GPS is
// requested but is near-useless indoors (typically 10-50m accuracy, and
// altitude is too noisy for floor detection), so it is recorded for context
// only and never used for in-building placement.
//
// Feature-detect everything; never assume a capability exists just because
// it does on some other phone. Report what THIS device actually offers via
// /capabilities/<id> before any permission is requested, then again after,
// so denials/failures are visible from the server side too.
function detectCapabilities(){
  const c = {};
  c.deviceMotion = typeof DeviceMotionEvent !== 'undefined';
  c.deviceMotionNeedsPermission = c.deviceMotion && typeof DeviceMotionEvent.requestPermission === 'function';
  c.deviceOrientation = typeof DeviceOrientationEvent !== 'undefined';
  c.deviceOrientationNeedsPermission = c.deviceOrientation && typeof DeviceOrientationEvent.requestPermission === 'function';
  c.deviceOrientationAbsoluteEvent = 'ondeviceorientationabsolute' in window;
  c.webkitCompassHeading = 'unknown (reported after first orientation event, iOS only)';
  c.geolocation = !!navigator.geolocation;
  c.permissionsAPI = !!(navigator.permissions && navigator.permissions.query);
  c.genericSensors = {                    // feature-detected only; instantiation tried separately
    AbsoluteOrientationSensor: 'AbsoluteOrientationSensor' in window,
    LinearAccelerationSensor: 'LinearAccelerationSensor' in window,
    Accelerometer: 'Accelerometer' in window,
    Gyroscope: 'Gyroscope' in window,
    Magnetometer: 'Magnetometer' in window,
  };
  c.secureContext = window.isSecureContext;
  c.ua = navigator.userAgent.slice(0, 180);
  c.platform = navigator.platform || '';
  c.maxTouchPoints = navigator.maxTouchPoints || 0;
  return c;
}
let caps = detectCapabilities();
function postCaps(){
  try{ fetch('/capabilities/'+id, {method:'POST', headers:{'Content-Type':'application/json'},
       body: JSON.stringify(caps)}); }catch(e){}
}
postCaps();   // report feature-detection immediately, before any permission exists

let sensorFails = 0;
let sensor = {};              // latest flat snapshot -- same shape as before (gx/gy/gz/
                               // heading/pitch/roll/absolute/lat/lon/acc_m), so existing
                               // server-side readers (localize_snapshot) keep working
let motionBuf = [], orientBuf = [];   // high-rate raw samples, flushed and cleared often
let sensorsOn = false, motionCount = 0;
const sensBtn = document.getElementById('sensBtn'), sensStat = document.getElementById('sensStat');

function attachMotionListener(){
  window.addEventListener('devicemotion', e => {
    window.__motionSeen++;
    const lin = e.acceleration || {}, gr = e.accelerationIncludingGravity || {}, rr = e.rotationRate || {};
    sensor.gx = gr.x; sensor.gy = gr.y; sensor.gz = gr.z;
    sensor.rr_alpha = rr.alpha; sensor.rr_beta = rr.beta; sensor.rr_gamma = rr.gamma;
    motionBuf.push({t: performance.now()/1000, ax: lin.x, ay: lin.y, az: lin.z,
                     agx: gr.x, agy: gr.y, agz: gr.z,
                     rr_alpha: rr.alpha, rr_beta: rr.beta, rr_gamma: rr.gamma,
                     interval: e.interval});
    if(motionBuf.length > 2000) motionBuf.splice(0, motionBuf.length - 2000);  // safety cap
    motionCount++;
  }, true);
}
function attachOrientationListener(evName, forceAbsolute){
  window.addEventListener(evName, e => {
    const compass = (e.webkitCompassHeading !== undefined && e.webkitCompassHeading !== null)
      ? e.webkitCompassHeading : null;
    if(compass !== null && caps.webkitCompassHeading !== true){ caps.webkitCompassHeading = true; postCaps(); }
    sensor.heading = compass !== null ? compass : (e.alpha !== null ? (360 - e.alpha) % 360 : sensor.heading);
    sensor.pitch = e.beta; sensor.roll = e.gamma; sensor.absolute = !!e.absolute || !!forceAbsolute;
    orientBuf.push({t: performance.now()/1000, alpha: e.alpha, beta: e.beta, gamma: e.gamma,
                     absolute: !!e.absolute || !!forceAbsolute, compass_deg: compass});
    if(orientBuf.length > 2000) orientBuf.splice(0, orientBuf.length - 2000);
  }, true);
}
function attachGenericSensors(){
  // Generic Sensor API: most mobile browsers either lack it entirely (Safari)
  // or gate it behind a Permissions-Policy header this simple server doesn't
  // send, so this is expected to fail on most phones -- best-effort only,
  // every outcome (started / threw / runtime error) is recorded, never assumed.
  const tries = [
    ['LinearAccelerationSensor', s => { sensor.gs_ax = s.x; sensor.gs_ay = s.y; sensor.gs_az = s.z; }],
    ['Gyroscope', s => { sensor.gs_gx = s.x; sensor.gs_gy = s.y; sensor.gs_gz = s.z; }],
    ['AbsoluteOrientationSensor', s => { sensor.gs_quat = s.quaternion; }],
    ['Magnetometer', s => { sensor.gs_mx = s.x; sensor.gs_my = s.y; sensor.gs_mz = s.z; }],
  ];
  for(const [name, onread] of tries){
    if(!(name in window)) continue;
    try{
      const sen = new window[name]({frequency: 30});
      sen.addEventListener('reading', () => onread(sen));
      sen.addEventListener('error', ev => {
        caps.genericSensors[name] = 'error: ' + (ev.error && ev.error.message || ev.error);
        postCaps(); rlog('generic-sensor', name + ': ' + (ev.error && ev.error.message));
      });
      sen.start();
      caps.genericSensors[name] = 'started';
    }catch(e){
      caps.genericSensors[name] = 'threw: ' + (e && e.message ? e.message : e);
      rlog('generic-sensor', name + ' construction failed: ' + e);
    }
  }
  postCaps();
}
function flushSensors(){
  if(!Object.keys(sensor).length && !motionBuf.length && !orientBuf.length) return;
  const payload = {latest: Object.assign({}, sensor), motion: motionBuf.splice(0), orientation: orientBuf.splice(0)};
  // A bridge restart rejects in-flight fetches. Swallow BOTH the sync throw
  // and the promise rejection: an unhandled rejection here used to kill the
  // flush loop, leaving the device streaming video with no sensors at all.
  try{
    fetch('/sensor/'+id, {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify(payload)}).catch(e => { sensorFails++; });
  }catch(e){ sensorFails++; }
  if(sensorFails > 0 && sensorFails % 25 === 0){
    // the server went away and came back: re-post capabilities so it
    // knows this device again
    try{ postCaps(); }catch(e){}
  }
}
// iOS requires DeviceMotionEvent.requestPermission()/DeviceOrientationEvent.
// requestPermission() to be called from within a real user gesture, or it
// rejects outright -- so this is wired to an explicit button tap
// (sensBtn.onclick below), not to page load. Non-iOS platforms don't expose
// requestPermission at all, so those still start automatically on load.
async function enableSensors(explicit){
  if(sensorsOn) return;
  try{
    if(caps.deviceMotionNeedsPermission || caps.deviceOrientationNeedsPermission){
      if(!explicit){
        // iOS will not grant motion permission outside a user gesture, so this
        // cannot simply run on load. But the user always taps *something* -- so
        // claim the first tap anywhere on the page rather than demanding a
        // dedicated button press. The button is revealed only if that has not
        // happened after a few seconds.
        sensStat.textContent = 'tap anywhere to enable motion sensors';
        try{ const w=document.getElementById('sensWarn'); if(w) w.style.display='block'; }catch(e){}
        rlog('sensor-perm', 'awaiting user gesture (no motion until the screen is tapped)');
        if(!window.__sensGestureHooked){
          window.__sensGestureHooked = true;
          const grab = () => {
            document.removeEventListener('touchend', grab, true);
            document.removeEventListener('click', grab, true);
            rlog('sensor-perm', 'gesture captured -> requesting motion access');
            enableSensors(true);
          };
          document.addEventListener('touchend', grab, true);
          document.addEventListener('click', grab, true);
          setTimeout(function(){ if(!sensorsOn){ sensBtn.style.display = 'inline-block'; } }, 4000);
        }
        return;
      }
      if(caps.deviceMotionNeedsPermission){
        const rm = await DeviceMotionEvent.requestPermission();
        caps.motionPermission = rm; rlog('sensor-perm', 'motion: ' + rm);
        try{ if(rm === 'granted'){ const w=document.getElementById('sensWarn');
             if(w){ w.style.display='none'; } } }catch(e){}
        try{ if(rm === 'granted'){ const w=document.getElementById('sensWarn');
             if(w) w.style.display='none'; } }catch(e){}
        if(rm !== 'granted') sensStat.textContent = 'motion permission denied -- steps/PDR unavailable on this device';
      }
      if(caps.deviceOrientationNeedsPermission){
        const ro = await DeviceOrientationEvent.requestPermission();
        caps.orientationPermission = ro; rlog('sensor-perm', 'orientation: ' + ro);
        if(ro !== 'granted') sensStat.textContent = (sensStat.textContent ? sensStat.textContent + ' | ' : '') +
          'orientation permission denied -- heading unavailable on this device';
      }
      postCaps();
      if(caps.motionPermission !== 'granted' && caps.orientationPermission !== 'granted' &&
         (caps.deviceMotionNeedsPermission || caps.deviceOrientationNeedsPermission)){
        return;   // both denied: nothing to attach, but capabilities are still recorded
      }
    }
    if(!caps.deviceMotionNeedsPermission || caps.motionPermission === 'granted') attachMotionListener();
    if(!caps.deviceOrientationNeedsPermission || caps.orientationPermission === 'granted'){
      attachOrientationListener('deviceorientation', false);
      if(caps.deviceOrientationAbsoluteEvent) attachOrientationListener('deviceorientationabsolute', true);
    }
    attachGenericSensors();
    if(navigator.geolocation){
      navigator.geolocation.watchPosition(
        pos => {
          const c = pos.coords;
          sensor.lat = c.latitude; sensor.lon = c.longitude; sensor.acc_m = c.accuracy;
          sensor.alt = c.altitude; sensor.alt_acc_m = c.altitudeAccuracy;
          sensor.gps_heading = c.heading; sensor.gps_speed = c.speed;
          if(caps.geolocationGranted !== true){ caps.geolocationGranted = true; postCaps(); }
        },
        err => { caps.geolocationGranted = 'denied: ' + err.message; postCaps(); rlog('geo', err.message); },
        {enableHighAccuracy: true, maximumAge: 5000});
    }
    sensorsOn = true;
    sensBtn.textContent = 'Sensors ON'; sensBtn.disabled = true;
    setInterval(flushSensors, 200);         // batched high-rate flush, ~5x/s
    setInterval(() => {
      const hz = (motionCount / 2).toFixed(1);   // 2s measurement window
      sensStat.textContent = (sensorFails ? '[' + sensorFails + ' send fails] ' : '') +
        'motion ~' + hz + 'Hz  heading=' +
        (sensor.heading != null ? Math.round(sensor.heading) + 'deg' : '—') +
        '  gps acc=' + (sensor.acc_m != null ? Math.round(sensor.acc_m) + 'm' : '—');
      motionCount = 0;
    }, 2000);
    rlog('sensor', 'sensor streaming started (explicit=' + explicit + ')');
  }catch(e){ sensStat.textContent = 'sensor setup failed: ' + e; rlog('sensor', 'failed: ' + e); }
}
sensBtn.onclick = () => enableSensors(true);
window.addEventListener('error', e => rlog('js-error', e.message + ' @' + e.filename + ':' + e.lineno));
window.addEventListener('unhandledrejection', e => rlog('promise', e.reason));
// auto-name the device from its user agent so nobody has to type anything
function autoName(){
  const u = navigator.userAgent;
  let kind = 'device';
  if(/iPhone/i.test(u)) kind='iPhone';
  else if(/iPad/i.test(u)) kind='iPad';
  else if(/Android/i.test(u)) kind = /Mobile/i.test(u) ? 'Android' : 'AndroidTablet';
  else if(/Macintosh/i.test(u)) kind='Mac';
  else if(/Windows/i.test(u)) kind='Windows';
  return kind + '-' + id.slice(0,4).toUpperCase();
}
document.getElementById('nm').value = localStorage.getItem('rcnm') || autoName();
document.getElementById('who').textContent =
  'this device: ' + document.getElementById('nm').value;
let running=false, sent=0, failstreak=0, mode='MAP', cvRef=null, snapTick=0;
// mode toggles removed: always stream, always allow a capture
const snapRow=document.getElementById('snapRow'), capBtn=document.getElementById('capBtn');
const genBtn=document.getElementById('genBtn'), presetSel=document.getElementById('preset');
const s2=document.getElementById('s2'), genimg=document.getElementById('genimg');
let lastPoseOk=false;
capBtn.onclick=async ()=>{
  if(!cvRef || !cvRef.width){
    s2.textContent = running ? 'no camera frame yet - wait a moment'
                             : 'camera not started - tap Start first';
    rlog('snapshot', 'capture blocked: cvRef=' + (!!cvRef) + ' running=' + running);
    return;
  }
  s2.textContent='capturing + localizing on the plan...';
  genBtn.disabled=true; lastPoseOk=false;
  const b=await new Promise(r=>cvRef.toBlob(r,'image/jpeg',0.85));
  try{
    const resp=await fetch('/snapshot/'+id,{method:'POST',body:b});
    const j=await resp.json();
    if(j.ok){
      lastPoseOk=true; genBtn.disabled=false;
      const p=j.pose;
      s2.textContent='placed: room='+(p.room_id||'?')+'  conf='+p.confidence+
        '  heading='+Math.round(p.heading_deg)+'deg  ('+p.elapsed_s+'s via '+p.method+')';
      // Capturing is only ever a step towards generating, so do not make the
      // user find a second button. Reuses genBtn's path rather than repeating
      // it -- duplicating this logic is how the shutter drifted out of sync.
      rlog('snapshot', 'capture ok -> auto-generating ' + presetSel.value);
      genBtn.click();
    }else{
      s2.textContent='localization failed: '+(j.error||'unknown error');
    }
  }catch(e){ s2.textContent='capture failed: '+e; }
};

const shutter = document.getElementById('shutter');   // removed from the UI
const shutterHint = document.getElementById('shutterHint');
if(shutter) shutter.onclick = async () => {
  rlog('shutter', 'tapped; running=' + (typeof running !== 'undefined' ? running : '?') +
       ' canvas=' + (cvRef ? (cvRef.width + 'x' + cvRef.height) : 'null'));
  // One tap does the whole job. Requiring Capture then Generate meant the
  // common case took two presses and looked broken if either was missed.
  if(!cvRef || !cvRef.width){
    shutterHint.textContent = running ? 'waiting for camera...' : 'tap Start first';
    rlog('shutter', 'ABORTED before capture: no camera frame yet');
    return;
  }
  shutter.disabled = true; shutter.classList.add('busy');
  shutterHint.textContent = 'capturing...';
  try{
    const b = await new Promise(r => cvRef.toBlob(r, 'image/jpeg', 0.9));
    const resp = await fetch('/snapshot/' + id, {method:'POST', body:b});
    const j = await resp.json();
    rlog('shutter', 'snapshot http=' + resp.status + ' ok=' + j.ok +
         (j.error ? (' err=' + j.error) : ''));
    if(!j.ok){
      shutterHint.textContent = 'capture failed: ' + (j.error || 'unknown');
      shutter.disabled = false; shutter.classList.remove('busy'); return;
    }
    lastPoseOk = true;
    shutterHint.textContent = 'generating ' + presetSel.value + '...';
    rlog('shutter', 'generate requested preset=' + presetSel.value);
    await fetch('/generate/' + id + '?preset=' + encodeURIComponent(presetSel.value),
                {method:'POST'});
    const poll = async () => {
      try{
        const r = await fetch('/generate/' + id); const g = await r.json();
        if(g.status === 'done'){
          genimg.src = '/genimg/' + id + '?t=' + Date.now();
          genimg.style.display = 'block';
          shutterHint.textContent = 'done in ' + g.total_s + 's - tap again for another';
          rlog('shutter', 'DONE in ' + g.total_s + 's -> shown on Spark');
          shutter.disabled = false; shutter.classList.remove('busy'); return;
        }
        if(g.status === 'error'){
          shutterHint.textContent = 'generate failed: ' + g.error;
          rlog('shutter', 'GENERATE FAILED: ' + g.error);
          shutter.disabled = false; shutter.classList.remove('busy'); return;
        }
        setTimeout(poll, 1500);
      }catch(e){
        shutterHint.textContent = 'lost connection while generating';
        shutter.disabled = false; shutter.classList.remove('busy');
      }
    };
    setTimeout(poll, 1200);
  }catch(e){
    shutterHint.textContent = 'capture error: ' + e;
    rlog('shutter', 'CAPTURE THREW: ' + e);
    shutter.disabled = false; shutter.classList.remove('busy');
  }
};

// Motion needs a user gesture on iOS, but it must not cost the user a tap of
// its own: claim the FIRST touch anywhere, whatever it was for. The camera does
// not wait for this -- it starts on load.
(function(){
  let asked = false;
  const ask = () => {
    if(asked) return;
    asked = true;
    rlog('sensor-perm', 'first touch -> requesting motion + orientation');
    try{ enableSensors(true); }catch(e){ rlog('sensor-perm','request threw: '+e); }
  };
  document.addEventListener('touchstart', () => {
    ask();
    // getUserMedia may have been deferred to a gesture: retry here too, so the
    // feed comes up on the same touch rather than needing a Start button.
    try{ if(!running) begin(false); }catch(e){}
  }, {capture:true, passive:true});
  document.addEventListener('click', ask, true);
})();

// --- motion permission recovery -----------------------------------------
// The prompt cannot be re-triggered after a denial, so surface the state and
// the fix, and keep retrying cheaply in case it is corrected in Settings.
window.__motionSeen = 0;
(function(){
  const panel = document.getElementById('permHelp');
  const why   = document.getElementById('permWhy');
  const retry = document.getElementById('permRetry');
  if(!panel) return;

  const show = (reason) => {
    if(why) why.textContent = reason || '';
    panel.style.display = 'block';
  };
  const hide = () => { panel.style.display = 'none'; };

  const attempt = async () => {
    try{
      const r = await enableSensors(true);
      rlog('sensor-perm', 'retry -> ' + r);
    }catch(e){ rlog('sensor-perm', 'retry threw: ' + e); }
  };
  if(retry) retry.onclick = attempt;

  // Any tap is a fresh user activation: cheap to retry, and it recovers
  // instantly if the setting was just changed.
  document.addEventListener('touchend', () => {
    if(panel.style.display === 'block') attempt();
  }, true);

  // Watchdog: what matters is whether samples actually arrive.
  setInterval(() => {
    const needs = (typeof DeviceMotionEvent !== 'undefined' &&
                   typeof DeviceMotionEvent.requestPermission === 'function');
    if(window.__motionSeen > 0){
      hide();
      try{ const w=document.getElementById('sensWarn');
           if(w) w.style.display='none'; }catch(e){}
      return;
    }
    try{ const w=document.getElementById('sensWarn');
         if(w && w.style.display === 'none') w.style.display='block'; }catch(e){}
    if(!needs) return;                       // no gate on this platform
    const st = (window.caps && caps.motionPermission) || 'unknown';
    if(st === 'denied'){
      show('You chose "Don\'t Allow", and iOS will not ask again.');
    }else if(performance.now() > 9000){
      show('No motion data is arriving' +
           (st === 'granted' ? ' even though access was granted.' : '.'));
    }
  }, 2500);
})();
let polling=false;
genBtn.onclick=async ()=>{
  if(!lastPoseOk||polling) return;
  polling=true; genBtn.disabled=true; genimg.style.display='none';
  s2.textContent='generating ('+presetSel.value+')... this can take up to a minute the first time';
  try{
    await fetch('/generate/'+id+'?preset='+encodeURIComponent(presetSel.value),{method:'POST'});
    const poll=async ()=>{
      try{
        const r=await fetch('/generate/'+id); const j=await r.json();
        if(j.status==='done'){
          genimg.src='/genimg/'+id+'?t='+Date.now(); genimg.style.display='block';
          s2.textContent='done — render '+j.render_s+'s + generate '+j.gen_s+'s = '+j.total_s+'s total';
          polling=false; genBtn.disabled=false; return;
        }else if(j.status==='error'){
          s2.textContent='generate failed: '+j.error; polling=false; genBtn.disabled=false; return;
        }
        s2.textContent='generating... ('+(j.status||'running')+')';
        setTimeout(poll,1500);
      }catch(e){ s2.textContent='poll failed: '+e; polling=false; genBtn.disabled=false; }
    };
    setTimeout(poll,1500);
  }catch(e){ s2.textContent='generate request failed: '+e; polling=false; genBtn.disabled=false; }
};
// report the environment immediately: secure-context problems are the usual cause
// of a denied camera, and they are invisible from the server side otherwise.
rlog('open', 'page opened; secureContext=' + window.isSecureContext +
     ' proto=' + location.protocol +
     ' mediaDevices=' + !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia));
const v=document.getElementById('v'), s=document.getElementById('s'), go=document.getElementById('go');
async function begin(auto){
  if(running) return;
  const nm=document.getElementById('nm').value||'phone';
  localStorage.setItem('rcnm',nm);
  try{
    // rear camera preferred; falls back to any available
    const st = await navigator.mediaDevices.getUserMedia(
      {video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}},audio:false});
    v.srcObject = st; running=true; go.disabled=true; go.textContent='Streaming';
    const cv=document.createElement('canvas'), cx=cv.getContext('2d');
    cvRef = cv;                        // let the SNAPSHOT capture button reuse this canvas
    const tick = async () => {
      if(!running) return;
      if(v.videoWidth){
        cv.width=Math.min(960,v.videoWidth); cv.height=cv.width*v.videoHeight/v.videoWidth;
        cx.drawImage(v,0,0,cv.width,cv.height);
        // Keep feeding the desktop app in SNAPSHOT mode too, at a
        // lower rate. Stopping uploads made the device go stale and
        // its position freeze on the map the moment you switched.
        snapTick = (snapTick + 1) % 6;
        if(true){
          const b = await new Promise(r=>cv.toBlob(r,'image/jpeg',0.6));
          if(b){ try{
            await fetch('/frame/'+id+'?name='+encodeURIComponent(nm),{method:'POST',body:b})
                  .catch(e => { failstreak++; });
            sent++; failstreak=0;
            s.textContent='MAP — streaming — '+sent+' frames — '+cv.width+'x'+Math.round(cv.height);
          }catch(e){ s.textContent='send failed: '+e;
                     if(++failstreak<=3) rlog('upload', e); } }
        } else {
          s.textContent='SNAPSHOT mode — camera live ('+cv.width+'x'+Math.round(cv.height)+'), tap Capture';
        }
      }
      setTimeout(tick, 120);           // ~8 fps, gentle on a loaded box
    };
    tick();
  }catch(e){
    // First visit needs a user gesture; browsers reject an unprompted call.
    // Surface the button only then, so repeat visits start hands-free.
    if(auto){
      go.style.display='';
      s.textContent = 'tap Start to share this camera';
      rlog('autostart', 'deferred to gesture: ' + (e && e.name ? e.name : e));
      return;
    }
    const hint = !window.isSecureContext
      ? ' — page is NOT a secure context; browsers block the camera outside https/localhost'
      : (e && e.name === 'NotAllowedError' ? ' — permission dismissed or blocked in site settings'
      : (e && e.name === 'NotFoundError' ? ' — no camera found on this device' : ''));
    s.textContent = 'camera unavailable: ' + (e && e.name ? e.name : e) + hint;
    rlog('getusermedia', (e && e.name ? e.name+': ' : '') + (e && e.message ? e.message : e) + hint);
  }
};
go.onclick = () => {
  // Same gesture serves both: iOS only honours requestPermission inside one.
  try{ enableSensors(true); }catch(e){}
  begin(false);
};
// try immediately: if this origin already has camera permission, streaming
// begins with no interaction at all
window.addEventListener('load', () => setTimeout(() => {
  begin(true);          // auto-start: the feed is the point of this page
  // A previously-granted origin resolves immediately with no prompt, so a
  // returning phone comes up fully hands-free.
  try{ enableSensors(false); }catch(e){}
}, 250));
// sensors: non-iOS platforms have no requestPermission gate, so this
// silently succeeds there on load; iOS needs the explicit sensBtn tap above
// and enableSensors(false) here just posts the "tap to enable" hint.
window.addEventListener('load', () => {
  const w = document.getElementById('sensWarn');
  if(w){
    // Visible until real motion samples arrive: iOS needs a gesture, and
    // without a control there is no way to raise the prompt at all.
    w.style.display = 'block';
    w.onclick = () => {
      // One gesture, every prompt: motion, orientation, and the camera.
      rlog('sensor-perm', 'enable button tapped');
      enableSensors(true);
      try{ if(!running) begin(false); }catch(e){}
    };
  }
  setTimeout(() => enableSensors(false), 300);
});
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # Safari caches the page across server restarts, so UI
        # changes never reached the phone until site data was
        # cleared by hand. Force revalidation on every load.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if p == "/devices":
            now = time.time()
            with LOCK:
                d = [{"id": k, "name": v["name"], "age": round(now - v["ts"], 1),
                      "frames": v["n"], "live": (now - v["ts"]) < STALE_S,
                      "sensor": SENSORS.get(k, {})}
                     for k, v in DEVICES.items()]
            return self._send(200, json.dumps({"devices": d, "url": URL}))
        if p == "/errors":
            with LOCK:
                out = list(ERRORS)
            return self._send(200, json.dumps({"count": len(out), "errors": out[-80:]}))
        if p == "/capabilities":
            with LOCK:
                out = dict(CAPS)
            return self._send(200, json.dumps(out))
        if p.startswith("/capabilities/"):
            dev = p.split("/capabilities/")[1]
            with LOCK:
                c = CAPS.get(dev)
            if not c:
                return self._send(404, json.dumps({"error": "no capabilities reported yet for %r" % dev}))
            return self._send(200, json.dumps(c))
        if p.startswith("/motion/"):
            # debug helper: confirms the high-rate accel/gyro buffers used by
            # pdr.py are actually filling, without needing a phone in hand
            dev = p.split("/motion/")[1]
            with LOCK:
                m = list(MOTION_BUF.get(dev, []))
                o = list(ORIENT_BUF.get(dev, []))
            # Return the ACTUAL buffers, not just counts and a 10-sample peek.
            # Dead reckoning reads d["motion"]; this endpoint only ever provided
            # "motion_sample", so PDR received nothing every single call and the
            # position never translated while walking. The counts stay for
            # diagnostics; the arrays are what the consumer needs.
            return self._send(200, json.dumps({
                "device": dev, "motion_buffered": len(m), "orientation_buffered": len(o),
                "motion": m, "orientation": o,
                "motion_sample": m[-10:], "orientation_sample": o[-10:]}))
        if p.startswith("/latest/"):
            k = p.split("/latest/")[1]
            with LOCK:
                e = DEVICES.get(k)
            if not e:
                return self._send(404, json.dumps({"error": "unknown device"}))
            return self._send(200, e["jpeg"], "image/jpeg")
        if p.startswith("/generate/"):
            dev = p.split("/generate/")[1]
            return self._send(200, json.dumps(JOBS.get(dev, {"status": "none"})))
        if p.startswith("/viewpoint/"):
            dev = p.split("/viewpoint/")[1]
            v = VIEWPOINTS.get(dev)
            if not v:
                return self._send(404, json.dumps({"error": "no snapshot yet"}))
            return self._send(200, json.dumps(v))
        if p.startswith("/genimg/"):
            dev = p.split("/genimg/")[1]
            path = os.path.join(PLANS, "snap_gen_%s.png" % dev)
            if not os.path.exists(path):
                return self._send(404, json.dumps({"error": "no generated image yet"}))
            return self._send(200, open(path, "rb").read(), "image/png")
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        p = self.path.split("?")[0]
        if p.startswith("/sensor/"):
            # Body is either the OLD flat shape ({heading, pitch, gx, ...}, for
            # backward compat with anything still posting once/second) or the
            # NEW batched shape {latest: {...same flat shape...}, motion: [...],
            # orientation: [...]} the page now flushes ~5x/s. The high-rate
            # motion/orientation arrays are what pdr.py (TASK B) is meant to
            # consume; SENSORS[dev] stays the same "latest snapshot" shape
            # localize_snapshot() below has always read, untouched either way.
            dev = p.split("/sensor/")[1]
            n = int(self.headers.get("Content-Length", 0))
            try:
                rec = json.loads(self.rfile.read(n).decode())
            except Exception:
                rec = {}
            is_batch = isinstance(rec, dict) and ("latest" in rec or "motion" in rec or "orientation" in rec)
            latest = rec.get("latest", rec) if is_batch else rec
            motion = rec.get("motion") or [] if is_batch else []
            orient = rec.get("orientation") or [] if is_batch else []
            now = time.time()
            with LOCK:
                prev = dict(SENSORS.get(dev, {}))
                prev.update({k: v for k, v in (latest or {}).items() if v is not None})
                prev["ts"] = now
                SENSORS[dev] = prev
                mbuf = MOTION_BUF.setdefault(dev, collections.deque(maxlen=MOTION_BUF_MAX))
                mbuf.extend(motion[-MOTION_BUF_MAX:])
                obuf = ORIENT_BUF.setdefault(dev, collections.deque(maxlen=MOTION_BUF_MAX))
                obuf.extend(orient[-MOTION_BUF_MAX:])
                mcount, ocount = len(mbuf), len(obuf)
            return self._send(200, json.dumps({"ok": True, "motion_buffered": mcount,
                                                "orientation_buffered": ocount}))
        if p.startswith("/capabilities/"):
            dev = p.split("/capabilities/")[1]
            n = int(self.headers.get("Content-Length", 0))
            try:
                rec = json.loads(self.rfile.read(n).decode())
            except Exception:
                rec = {}
            rec["ts"] = time.time()
            rec["device"] = dev
            with LOCK:
                prev = CAPS.get(dev, {})
                prev.update(rec)
                CAPS[dev] = prev
            return self._send(200, json.dumps({"ok": True}))
        if p.startswith("/log/"):
            n = int(self.headers.get("Content-Length", 0))
            try:
                rec = json.loads(self.rfile.read(n).decode())
            except Exception:
                rec = {"kind": "unparsable"}
            rec["device"] = p.split("/log/")[1]
            rec["at"] = time.strftime("%H:%M:%S")
            rec["ip"] = self.client_address[0]
            with LOCK:
                ERRORS.append(rec)
                del ERRORS[:-MAX_ERRORS]
            print("[client %s/%s] %s: %s" % (rec.get("ip"), rec.get("name"),
                  rec.get("kind"), str(rec.get("detail"))[:150]), flush=True)
            return self._send(200, json.dumps({"ok": True}))
        if p.startswith("/snapshot/"):
            dev = p.split("/snapshot/")[1]
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n)
            buf = np.frombuffer(data, np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            # Persist the capture BEFORE any model runs. Depth can fail on a
            # memory-starved box, and losing the photo with it meant no image
            # could ever be generated.
            try:
                if img is not None:
                    _fp = os.path.join(PLANS, 'capture_%s.jpg' % dev)
                    cv2.imwrite(_fp, img)
                    LAST_FRAME[dev] = _fp
            except Exception:
                pass
            if img is None:
                return self._send(400, json.dumps({"ok": False, "error": "bad image"}))
            # also cache as this device's latest frame, so /devices and /latest/
            # keep working normally for it even while it's in SNAPSHOT mode
            with LOCK:
                prev = DEVICES.get(dev, {"n": 0, "name": "phone"})
                DEVICES[dev] = {"jpeg": data, "ts": time.time(),
                                "name": prev.get("name", "phone"), "n": prev.get("n", 0) + 1}
            pose = localize_snapshot(dev, img)
            if "error" in pose and "degraded" not in pose:
                return self._send(200, json.dumps({"ok": False, "error": pose["error"]}))
            VIEWPOINTS[dev] = pose
            return self._send(200, json.dumps({"ok": True, "pose": pose}))
        if p.startswith("/generate/"):
            dev = p.split("/generate/")[1]
            preset = "dentist office"
            if "?" in self.path and "preset=" in self.path:
                from urllib.parse import parse_qs
                preset = parse_qs(self.path.split("?", 1)[1]).get("preset", [preset])[0]
            cur = JOBS.get(dev)
            if cur and cur.get("status") == "running":
                return self._send(200, json.dumps({"ok": True, "status": "running"}))
            th = threading.Thread(target=run_generate, args=(dev, preset), daemon=True)
            th.start()
            return self._send(200, json.dumps({"ok": True, "status": "started", "preset": preset}))
        if not p.startswith("/frame/"):
            return self._send(404, json.dumps({"error": "not found"}))
        name = "phone"
        if "?" in self.path and "name=" in self.path:
            from urllib.parse import unquote, parse_qs
            name = parse_qs(self.path.split("?", 1)[1]).get("name", ["phone"])[0]
        n = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(n)
        k = p.split("/frame/")[1]
        with LOCK:
            prev = DEVICES.get(k, {"n": 0})
            DEVICES[k] = {"jpeg": data, "ts": time.time(),
                          "name": name, "n": prev.get("n", 0) + 1}
        return self._send(200, json.dumps({"ok": True}))


def make_qr(path):
    """QR for the join URL. Uses qrcode if present, else writes the URL only."""
    try:
        import qrcode
        img = qrcode.make(URL)
        img.save(path)
        return True
    except Exception as e:
        print("[bridge] qrcode unavailable (%s); URL only" % e, flush=True)
        return False


if __name__ == "__main__":
    qr_path = os.path.expanduser("~/plans/phone_qr.png")
    ok = make_qr(qr_path)
    print("[bridge] join URL : %s" % URL, flush=True)
    print("[bridge] QR       : %s" % (qr_path if ok else "not generated"), flush=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    if USE_TLS:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT, KEY)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        print("[bridge] TLS enabled (self-signed - expect a browser warning)", flush=True)
    else:
        print("[bridge] WARNING: plain HTTP; browsers will DENY the camera", flush=True)
    print("[bridge] listening on 0.0.0.0:%d" % PORT, flush=True)
    srv.serve_forever()
