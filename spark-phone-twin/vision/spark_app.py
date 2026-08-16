import glob
"""Recast / Build Vitals — single tabbed app for the Spark desktop.

One view, no tabs: joined phones down the left (live 2D above each device's own
3D reconstruction), the building on the right with the scenegraph tree overlaid
on the geometry it describes, and the join QR always on screen.

  drag = orbit (tabs 2/3)   scroll = zoom   r = reset view   q/esc = quit
  c = clear accumulated geometry   f = cycle floors   s = save scan
  [ ] = rotate scan   e = export PLY + mesh   p = generate image   n = preset

Phones are the only sensor. The floor plan supplies the drift-free skeleton and
metric truth; each phone frame is turned into a metric cloud, anchored to a room
by phone_slam, and voxel-accumulated so walking the building fills the shell in
with real surfaces the 2D plan never had.

Depth is YOLO26-depth: measured 13.1 ms/frame vs 239 ms for Depth-Anything-V2 on
this box, at r=0.913 agreement — same geometry, 18x the speed, so three phones
reconstruct concurrently in real time. Residual scale error is absorbed by the
plan anchoring, which is what makes the fast model safe to use.
"""
import os, sys, time, json, threading, urllib.parse
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phone_slam
import plan_render
import scenegraph3d
import ceiling
import localize
import scan_session
import pdr
import qr_calibrate
import twin_db

PLANS = os.path.expanduser("~/plans")
CEIL = 9 * 0.3048 + 7 * 0.0254
MAIN_CEIL = 15 * 0.3048 + 4 * 0.0254
F2F = MAIN_CEIL + 0.60
def _screen_size(default=(1360, 768)):
    """Size the window to the actual display — a 1600x900 canvas on a 1360x768
    screen puts the right-hand content off the edge."""
    try:
        import subprocess
        # xdpyinfo needs DISPLAY/XAUTHORITY explicitly: launched via nohup from a
        # non-login shell it inherits neither, returns nothing, and the window
        # silently falls back to a default size.
        env = dict(os.environ)
        env.setdefault("DISPLAY", ":1")
        env.setdefault("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
        o = subprocess.run(["xdpyinfo"], capture_output=True, text=True,
                           timeout=5, env=env).stdout
        for ln in o.splitlines():
            if "dimensions:" in ln:
                w, h = ln.split()[1].split("x")
                if int(w) > 200 and int(h) > 200:
                    return int(w), int(h)
    except Exception:
        pass
    return default


W, H = _screen_size()
# leave room for the window manager's frame. Sizing the canvas to the full screen
# meant the window could not show it 1:1, so every frame was scaled — and when I
# tried tracking the window rect instead, the chrome got counted too and the
# content letterboxed with white bars. A fixed, slightly smaller canvas avoids both.
H = max(480, H - 96)
W = max(800, W - 24)
TAB_H = 42
PERSON = 0
HFOV_DEG = 68.0     # phone hFOV — a GUESS; fov_calibrate.py can measure it
TABS = ["LIVE", "ROOMS 3D", "BUILDING", "JOIN"]
SG_PATH = os.path.expanduser("~/plans/scenegraph.json")
view = dict(tab=0, yaw=25.0, pitch=55.0, dist=None, drag=False, mx=0, my=0,
            levels="both")      # both | level1 | level2 — an opaque upper floor
                                # correctly hides the one below it, so you need
                                # a way to look at each storey

BRIDGE = os.environ.get("BRIDGE_URL", "https://127.0.0.1:8099")
QR_PATH = os.path.expanduser("~/plans/phone_qr.png")
phones = {"list": [], "url": "", "frames": {}}
SENSOR_HIST = {}   # dev id -> {key: [values]} short history, for the sparklines
SENSOR_KEYS = ("heading", "gx", "gy", "gz", "rr_alpha", "rr_beta", "rr_gamma",
               "acc_m", "alt", "lat", "lon")
SENSORS = {}     # dev id -> latest sensor dict, taken straight from /devices.
                 # /sensor/<id> was returning {"error": ...} for live devices, so
                 # every frame saw no gravity and no heading — which is exactly
                 # why the direction never changed.
_plock = threading.Lock()
stop = False


# ---------------- plan geometry: the skeleton everything anchors to ----------
try:
    if twin_db.init_schema():
        print("[db] session storage ready (recast)", flush=True)
    else:
        print("[db] session storage unavailable; capture continues", flush=True)
except Exception as _e:
    print("[db] init skipped: %s" % str(_e)[:80], flush=True)
print("[init] loading plan geometry ...", flush=True)
LEVELS = {}
for lv, base_z in (("level1", 0.0), ("level2", F2F)):
    # Prefer the aligned geometry: the two sheets were extracted in separate page
    # coordinates, leaving level 2 offset ~8.8 m east of level 1. Cross-correlating
    # the wall rasters recovered the datum (see level_alignment.json).
    # _clean sets drop drafting annotation. Nearly half the extracted segments
    # were grid centerlines, title block and an egress travel-distance diagram —
    # all of it was being extruded into 3D as if it were wall.
    wp = None
    for cand in ("%s_walls_m_v3.npy", "%s_walls_m_clean_aligned.npy",
                 "%s_walls_m_clean.npy", "%s_walls_m_aligned.npy",
                 "%s_walls_m.npy"):
        c = "%s/%s" % (PLANS, cand % lv)
        if os.path.exists(c):
            wp = c
            break
    # _rooms_v2 comes from the wall network. The original extraction produced
    # polygons that crossed walls and corridors, so room matching anchored to
    # them was matching against shapes that are not rooms.
    rp = "%s/%s_rooms_v2_aligned.json" % (PLANS, lv)
    for alt in ("%s/%s_rooms_v2.json" % (PLANS, lv), "%s/%s_rooms.json" % (PLANS, lv)):
        if os.path.exists(rp):
            break
        rp = alt
    if not wp:
        continue
    LEVELS[lv] = dict(
        walls=np.load(wp), base_z=base_z,
        rooms=json.load(open(rp)) if os.path.exists(rp) else [],
        acc=phone_slam.Accumulator(voxel=0.06))
    q = plan_render.wall_quads(LEVELS[lv]["walls"], base_z, CEIL)
    base_col = (150, 205, 240) if lv == "level1" else (235, 190, 130)
    # An opaque floor plate per storey. Without one you look straight between
    # level 2's walls and see level 1 below it — floors are not transparent.
    slab = plan_render.floor_slab(LEVELS[lv]["walls"], base_z)
    LEVELS[lv]["walls_only"] = q
    LEVELS[lv]["wcols_only"] = plan_render.quad_shade(q, base_col)
    LEVELS[lv]["quads"] = np.concatenate([slab, q])
    LEVELS[lv]["qcols"] = np.concatenate([
        np.array([[int(c * 0.34) for c in base_col]], np.uint8),
        LEVELS[lv]["wcols_only"]])
    print("[init]   %s: %d walls (%s), %d rooms (%s), %d quads" %
          (lv, len(LEVELS[lv]["walls"]), os.path.basename(wp),
           len(LEVELS[lv]["rooms"]), os.path.basename(rp), len(q)), flush=True)

if LEVELS:
    _w = [d["walls"] for d in LEVELS.values()]
    bx0 = min(w[:, [0, 2]].min() for w in _w); bx1 = max(w[:, [0, 2]].max() for w in _w)
    by0 = min(w[:, [1, 3]].min() for w in _w); by1 = max(w[:, [1, 3]].max() for w in _w)
    SPAN = max(bx1 - bx0, by1 - by0)
    BCEN = np.array([(bx0 + bx1) / 2, (by0 + by1) / 2, F2F / 2], np.float32)
else:
    bx0 = by0 = 0.0; bx1 = by1 = 30.0; SPAN = 30.0
    BCEN = np.array([15, 15, 2], np.float32)
view["dist"] = SPAN * 1.05

# Known ceiling heights (9'-7" standard, 15'-4" main room) are a localization
# signal, not just a rendering detail: two rooms with identical footprints are
# indistinguishable by shape but trivially separated by height.
ROOM_HEIGHTS = {}
_chp = "%s/ceiling_heights.json" % PLANS
if os.path.exists(_chp):
    try:
        ROOM_HEIGHTS = (json.load(open(_chp)) or {}).get("rooms", {})
        print("[init] ceiling heights for %d rooms" % len(ROOM_HEIGHTS), flush=True)
    except Exception as e:
        print("[init] ceiling heights unreadable: %s" % str(e)[:60], flush=True)

# the live tree: plan supplies levels/rooms, phone video supplies the leaves
SG = scenegraph3d.SceneGraph(LEVELS)

# pick up where the last run left off rather than starting from an empty building
try:
    _prev = scan_session.load("autosave")
    if _prev:
        _n = scan_session.restore_into(LEVELS, _prev)
        print("[init] restored %s: %s points from %s"
              % ("autosave", "{:,}".format(_n),
                 _prev.get("meta", {}).get("saved", "?")), flush=True)
except Exception as e:
    print("[init] no prior scan restored (%s)" % str(e)[:60], flush=True)
_sglock = threading.Lock()

# Right-click the map to say where the join QR physically is. An absolute fix
# beats geometry here: a 68 deg view of two parallel corridor walls is genuinely
# ambiguous in a building with a repeating 6.4 m bay.
ANCHOR = dict(x=None, y=None, level="level1", heading_deg=None, set_at=0.0)

# Scale calibration from the anchor QR. Two unknowns multiply to distort the
# world - the assumed 68 deg field of view and the depth model's metric scale -
# and a printed QR of known size measures their product directly.
# Measure the WHITE square with a ruler — that is the easy, unambiguous
# dimension. The black pattern is smaller by the quiet zone, and that ratio is
# measured from the QR we actually print rather than assumed: getting it wrong
# scales the entire reconstruction (a 6.5in white square is a 12.5cm pattern,
# and calling it 15cm made everything 1.2x too large).
QR_WHITE_M = float(os.environ.get("QR_WHITE_M", str(6.5 * 0.0254)))


def _qr_pattern_ratio(path=QR_PATH, default=0.7576):
    try:
        g = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if g is None:
            return default
        ys, xs = np.nonzero(g < 128)
        if len(xs) < 100:
            return default
        rx = (xs.max() - xs.min() + 1) / float(g.shape[1])
        ry = (ys.max() - ys.min() + 1) / float(g.shape[0])
        r = float((rx + ry) / 2.0)
        return r if 0.5 < r < 0.98 else default
    except Exception:
        return default


QR_RATIO = _qr_pattern_ratio()
QR_SIZE_M = float(os.environ.get("QR_PATTERN_M", "0")) or (QR_WHITE_M * QR_RATIO)
print("[qr] white %.1f cm x ratio %.4f -> pattern %.1f cm"
      % (QR_WHITE_M * 100, QR_RATIO, QR_SIZE_M * 100), flush=True)
# Calibration converges and then LOCKS. Recomputing the median on every sighting
# meant the scale — and therefore every dimension derived from it — drifted
# forever, so nothing ever settled. Once the estimate is stable it is fixed
# until deliberately reset with [k].
CAL = dict(scale_k=1.0, samples=0, hfov_implied=None, spread=None, last=0.0,
           locked=False, shown_m=None)
CAL_MIN_SAMPLES = 3          # enough sightings to median away a bad frame
CAL_MAX_SPREAD = 0.12        # spread that counts as settled early
CAL_FORCE_SAMPLES = 10       # ...or simply enough reads: the MEDIAN of 10+ is
                             # stable even when individual reads scatter 15%,
                             # and demanding a tight spread meant never locking
_cal_hits = []
SUMMARY = dict(active=False, counts={}, t=0.0, name="", points=0)
SESSION = dict(started=False, db_id=None, t0=time.time())
# Generated repurposing images, shown on the desktop as soon as the phone's
# shutter produces one — the operator is watching this screen, not the phone.
GEN_IMG = {}        # dev id -> (image, preset, mtime)
GEN_PRESETS = ["dentist office", "condo", "coworking space", "medical clinic",
               "cafe", "retail", "fitness studio", "private office suite"]
CAPTURES = []          # [(path, dev, mtime)] every photo taken, newest first
CAP_SEL = [0]          # which capture the next generation uses
PRESET_OPEN = [False]  # dropdown expanded?
_HIT = {}              # click regions: name -> (x0, y0, x1, y1)
_TURN_LOG = {}         # rate-limit the heading-jump reports
_TP_LOG = {}           # rate-limit teleport reports
_PDR_QUIET = {}        # rate-limit the idle report
_PDR_SEEN = {}         # samples already consumed, to avoid double-counting
_PDR_CLOCK = {}        # per-device synthetic clock: phones send no timestamps
SHOT_EVERY = float(os.environ.get("SHOT_EVERY", "0"))   # seconds; 0 = off
SHOT_DIR = os.path.expanduser("~/plans/shots")
_SHOT = {"t": 0.0, "n": 0}
SHOW_QR = [False]      # [b] forces the join QR back on screen
SHOW_TREE = [False]   # [j] toggles; the map stays clear by default
_HANDOFF = {}      # last pose handed to the renderer, per device
_MARKER_LOG = {}   # last logged marker world position per device
_GEN_DRAW = {}     # draw-state, so the log reports each new image once
GEN_SEL = [0]       # [n] cycles the preset, [p] generates for the first phone
OBJ_SIZE_SEEN = {}  # (dev, cls) -> last measured extent, shown on the feed
DEPTH_VIEW = {}     # dev id -> colourised depth, for the view toggle
VIEW_MODE = ["video"]   # video | depth | points — cycled with [v]
SENSOR_SHOW = [True]    # [g] toggles the sensor readout + sparklines
# The floor slab is a bounding-box rectangle, so it covers ground well outside
# the building and visually swamps the walls — the plan stops being readable.
# Off by default; [o] brings it back when you want solid storeys.
SLAB_SHOW = [False]
# Every frame's inputs and outputs, appended to disk. Debugging "position does
# not translate" from a live walk is impossible without the history: which
# sensors arrived, how many motion samples, what PDR returned, what the pose
# did. Written as JSONL so it can be replayed and diffed.
TRACE_PATH = os.path.expanduser("~/plans/pose_trace.jsonl")
TRACE_ON = [True]
# Without landmarks the pose falls back to a room-centroid match, so walking
# inside a corridor does not move you at all — position is quantised to rooms.
# Dead reckoning supplies the translation between fixes.
_pdr = {}               # dev id -> (PDRTracker, last_sample_t)
_flow_prev = {}         # dev id -> previous small grey frame, for optical flow
FLOW_STILL_PX = 0.06    # 0.6 froze 60 of 122 frames of an actual walk: at
                        # 160x120 a walking phone produces small median flow, so
                        # the threshold has to mean "genuinely static", not "slow"
QR_SEEN = {}        # dev id -> last QR sighting, drawn on that phone's feed so
                    # the operator can check the measurement against the ruler
# The run begins with the operator telling us the one thing geometry cannot
# reliably recover in this building: where the phones start from.
# Remember where the QR was last placed: re-clicking the same spot every run is
# both tedious and a source of inconsistency between sessions.
_prev_anchor = None
try:
    import anchors as _anch_boot
    for _a in _anch_boot.load().get("anchors", []):
        if _a.get("id") in ("start", "manual"):
            _prev_anchor = _a
            break
except Exception:
    _prev_anchor = None

LOCK_LEVEL = [None]      # a phone stays on the storey it was anchored to:
                         # walking never changes floor, so a level flip is an
                         # error, and it makes the marker vanish from view

SETUP = dict(active=True, level="level1", x=None, y=None, map=None,
             heading=None)   # which way the QR faces; the 180-deg flip is our
                             # worst failure, and the operator simply knows this
if _prev_anchor:
    SETUP.update(level=_prev_anchor.get("level", "level1"),
                 x=_prev_anchor.get("x"), y=_prev_anchor.get("y"),
                 heading=_prev_anchor.get("heading_deg"))
    # A complete saved anchor answers every question the setup screen asks, so
    # do not block the whole dashboard waiting to be told again.
    if SETUP["heading"] is None:
        SETUP["heading"] = 0.0        # compass overrides this once a phone reports
    # Always confirm the anchor on startup: it is the origin of every position
    # measurement, and silently reusing a stale one hides a wrong datum.
    LOCK_LEVEL[0] = SETUP["level"]
    print("[setup] previous anchor (%.2f, %.2f) on %s preloaded - "
          "click to move it, [enter] to accept"
          % (SETUP["x"], SETUP["y"], SETUP["level"]), flush=True)
    if os.environ.get("SETUP_AUTOSTART") == "1":
        # unattended runs (the pose test, screenshots) accept the saved anchor
        SETUP["active"] = False
        print("[setup] SETUP_AUTOSTART=1 -> accepted without a keypress",
              flush=True)
    if False:
        SETUP["active"] = False
        LOCK_LEVEL[0] = SETUP["level"]
        print("[setup] resumed saved anchor: %s (%.2f, %.2f) heading %.0f deg "
              "- press [a] to re-anchor"
              % (SETUP["level"], SETUP["x"], SETUP["y"], SETUP["heading"]),
              flush=True)
    print("[setup] restored last QR position (%.1f, %.1f) on %s - [r] to reset"
          % (SETUP["x"], SETUP["y"], SETUP["level"]), flush=True)
_pose_prev = {}          # dev id -> (x, y, heading_deg, t, rejects)
# Compass headings increase CLOCKWISE from north; our map angles increase
# counter-clockwise. Without this sign the frustum turned the wrong way.
HEADING_SIGN = -1.0
HEAD_REF_STALE_S = 25.0  # re-seed if heading goes away and comes back, so a
                         # reference latched before sensors were granted cannot
                         # freeze the direction for the whole session
_head_ref = {}           # dev id -> (phone_heading, map_heading, t)
TRAILS = {}              # dev id -> [(x, y, level)] walked path, drawn on the map
TRAIL_MIN_STEP = 0.25    # metres between recorded points
TRAIL_MAX = 400
                         # Geometry yaw is snapped to the plan's Manhattan axis,
                         # so turning the phone 30 deg snapped back and the arrow
                         # never moved. Relative sensor rotation is not snapped.
POSE_ALPHA = 0.25        # 0 = frozen, 1 = no smoothing
MAX_SPEED = 2.0          # m/s — a brisk walk; anything faster is not a person
MAX_TURN = 220.0         # deg/s — faster than anyone turns while holding a phone
MAX_REJECTS = 8          # after this many, believe the new pose: we were wrong


# ---------------- models ----------------
from ultralytics import YOLO
import torch


def pick_device():
    """Other stack services hold the GPU; probe a real allocation, not is_available."""
    if not torch.cuda.is_available():
        return "cpu"
    try:
        t = torch.zeros(256, 256, device="cuda"); del t
        torch.cuda.empty_cache()
        return 0
    except Exception as e:
        print("[init] GPU unavailable (%s); using CPU" % str(e)[:60], flush=True)
        return "cpu"


DEV = pick_device()
IMGSZ = 640 if DEV != "cpu" else 448
print("[init] loading YOLO seg + depth on %s ..." % DEV, flush=True)
seg_model = YOLO("yolo11m-seg.pt")

depth_model, DEPTH_NAME = None, "none"
for cand in ("yolo26s-depth.pt", "yolo26n-depth.pt"):
    try:
        depth_model = YOLO(cand); DEPTH_NAME = cand
        print("[init] depth: %s" % cand, flush=True)
        break
    except Exception as e:
        print("[init] %s unavailable (%s)" % (cand, str(e).splitlines()[0][:60]), flush=True)
if depth_model is None:
    print("[init] NO depth model — 3D tabs will stay empty", flush=True)


def infer_depth(img):
    """Metric depth map for a BGR frame, or None. Result layout varies by
    ultralytics build, so probe the documented attributes rather than assume."""
    if depth_model is None:
        return None
    try:
        r = depth_model.predict(img, device=DEV, verbose=False)[0]
        for attr in ("depth", "depths"):
            o = getattr(r, attr, None)
            if o is None:
                continue
            d = o.data.cpu().numpy() if hasattr(o, "data") else np.asarray(o)
            d = np.squeeze(d).astype(np.float32)
            return d if d.ndim == 2 else None
    except Exception as e:
        print("[depth] %s" % str(e).splitlines()[0][:90], flush=True)
    return None


# ---------------- phone bridge ----------------



# The extracted plan is not accurate enough to constrain a phone: it lacks
# doors and invents barriers, which pins the marker against walls that are not
# there. Physical limits (speed, turn rate) still apply -- those hold whatever
# the map says.
WALL_CONSTRAIN = False   # retired: see the note above
MAX_TURN_DPS = 200.0     # deg/s: a brisk hand turn, far below a vision glitch
HEADING_ALPHA = 0.35     # circular smoothing on top of the rate limit


def damp_heading(dev_id, new_hd, now):
    """Rate-limit and smooth a heading update. None passes through."""
    if new_hd is None:
        return None
    pv = _pose_prev.get(dev_id)
    if pv is None or pv[2] is None:
        return float(new_hd) % 360.0
    prev = float(pv[2])
    dt = max(0.02, float(now) - float(pv[3]))
    d = ((float(new_hd) - prev + 180.0) % 360.0) - 180.0     # shortest turn
    lim = MAX_TURN_DPS * dt
    if abs(d) > lim:
        if time.time() - _TURN_LOG.get(dev_id, 0) > 5:
            _TURN_LOG[dev_id] = time.time()
            print("[pose] %s heading jump %.0f deg in %.2fs limited to %.0f"
                  % (dev_id, abs(d), dt, lim), flush=True)
        d = lim if d > 0 else -lim
    target = prev + d
    # circular exponential smoothing, so the arrow settles instead of twitching
    a = HEADING_ALPHA
    r0, r1 = np.radians(prev), np.radians(target)
    sx = (1 - a) * np.cos(r0) + a * np.cos(r1)
    sy = (1 - a) * np.sin(r0) + a * np.sin(r1)
    return float(np.degrees(np.arctan2(sy, sx)) % 360.0)


def clamp_move(dev_id, nx, ny, now, reason=""):
    """Limit a position update to what a walking person could cover."""
    pv = _pose_prev.get(dev_id)
    if pv is None:
        return float(nx), float(ny)
    dt = max(0.05, float(now) - float(pv[3]))
    dx, dy = float(nx) - pv[0], float(ny) - pv[1]
    d = float(np.hypot(dx, dy))
    lim = MAX_SPEED * dt
    if d <= lim or d < 1e-9:
        return float(nx), float(ny)
    f = lim / d
    if time.time() - _TP_LOG.get(dev_id, 0) > 5:
        _TP_LOG[dev_id] = time.time()
        print("[pose] %s rejected %.1fm jump in %.2fs (max %.2fm)%s"
              % (dev_id, d, dt, lim, (" [%s]" % reason) if reason else ""),
              flush=True)
    return pv[0] + dx * f, pv[1] + dy * f


def pdr_worker():
    """Advance dead reckoning for every live phone, independent of frames."""
    while not stop:
        try:
            with _plock:
                devs = [d["id"] for d in phones["list"]]
            if not devs:
                # Fall back to every device the bridge knows: "live" tracks
                # camera frames, and inertial tracking must not depend on those.
                try:
                    import urllib.request, ssl
                    _c = ssl.create_default_context()
                    _c.check_hostname = False
                    _c.verify_mode = ssl.CERT_NONE
                    with urllib.request.urlopen(BRIDGE + "/devices", timeout=2,
                                                context=_c) as _r:
                        _d = json.loads(_r.read().decode())
                    devs = [x["id"] for x in _d.get("devices", [])
                            if float(x.get("age", 1e9)) < 300.0]
                except Exception:
                    devs = []
            for dev_id in devs:
                pv = _pose_prev.get(dev_id)
                if pv is None:
                    # start from the anchor rather than waiting for a frame
                    ax_, ay_ = ANCHOR.get("x"), ANCHOR.get("y")
                    if ax_ is None or ay_ is None:
                        ax_, ay_ = SETUP.get("x"), SETUP.get("y")
                    if ax_ is None or ay_ is None:
                        continue
                    hd0 = ANCHOR.get("heading_deg")
                    if hd0 is None:
                        hd0 = SETUP.get("heading") or 0.0
                    pv = (float(ax_), float(ay_), float(hd0), time.time(), 0)
                    _pose_prev[dev_id] = pv
                    print("[pdr] seeded %s at anchor (%.2f, %.2f) heading %.0f"
                          % (dev_id, pv[0], pv[1], pv[2]), flush=True)
                hd = pv[2]
                sv = SENSORS.get(dev_id) or {}
                if sv.get("heading") is not None:
                    hd = float(sv["heading"])
                dxp, dyp, info = pdr_step(dev_id, hd)
                if abs(dxp) < 1e-4 and abs(dyp) < 1e-4:
                    try:
                        with open(TRACE_PATH, "a") as _tf:
                            _tf.write(json.dumps(dict(
                                t=round(time.time(), 3), dev=dev_id,
                                x=round(pv[0], 3), y=round(pv[1], 3),
                                heading=None if hd is None else round(float(hd), 1),
                                pdr_dx=0.0, pdr_dy=0.0,
                                steps=(info or {}).get("steps"),
                                source="pdr", moved=False)) + "\n")
                    except Exception:
                        pass
                    if time.time() - _PDR_QUIET.get(dev_id, 0) > 20:
                        _PDR_QUIET[dev_id] = time.time()
                        print("[pdr] %s no step this tick (%s)"
                              % (dev_id, (info or {}).get("error")
                                 or ("steps=%s" % (info or {}).get("steps"))),
                              flush=True)
                    continue
                nx, ny = pv[0] + dxp, pv[1] + dyp
                lv = None
                with _p3lock:
                    pc = phone3d.get(dev_id)
                    if pc:
                        lv = pc["meta"].get("level")
                        if LOCK_LEVEL[0] and lv != LOCK_LEVEL[0]:
                            lv = LOCK_LEVEL[0]      # never change floor by walking
                            pc["meta"]["level"] = lv
                    else:
                        # No camera frame has ever arrived for this device, so
                        # the frame path never made an entry. Create a pose-only
                        # one; otherwise a tracked phone is invisible.
                        lv = (LOCK_LEVEL[0] or ANCHOR.get("level")
                              or SETUP.get("level") or next(iter(LEVELS)))
                        if lv not in LEVELS:
                            lv = SETUP.get("level") or next(iter(LEVELS))
                        phone3d[dev_id] = dict(
                            cloud=np.zeros((0, 3), np.float32),
                            rgbc=np.zeros((0, 3), np.uint8),
                            name="phone (no video)",
                            meta=dict(level=lv, cam_pos=[pv[0], pv[1],
                                                         LEVELS[lv]["base_z"] + 1.5],
                                      heading_deg=hd, confidence="pdr",
                                      method="pdr"))
                        print("[pdr] %s marker created from dead reckoning "
                              "(no camera frame)" % dev_id, flush=True)
                # No wall blocking: the plan is not accurate enough to be a
                # constraint, and a wrong wall stops tracking outright.
                _now = time.time()
                nx, ny = clamp_move(dev_id, nx, ny, _now, "pdr")
                hd = damp_heading(dev_id, hd, _now)
                _pose_prev[dev_id] = (nx, ny, hd, _now, 0)
                with _p3lock:
                    pc = phone3d.get(dev_id)
                    if pc:
                        cp = pc["meta"].get("cam_pos")
                        z = cp[2] if isinstance(cp, (list, tuple)) and len(cp) > 2 else 0.0
                        pc["meta"]["cam_pos"] = [nx, ny, z]
                        pc["meta"]["heading_deg"] = hd
                tr_ = TRAILS.setdefault(dev_id, [])
                if lv and (not tr_ or float(np.hypot(nx - tr_[-1][0],
                                                     ny - tr_[-1][1])) > TRAIL_MIN_STEP):
                    tr_.append((nx, ny, lv))
                    del tr_[:-TRAIL_MAX]
                print("[pdr] %s walked (%.2f, %.2f) -> (%.2f, %.2f) steps=%s"
                      % (dev_id, dxp, dyp, nx, ny,
                         (info or {}).get("steps")), flush=True)
                try:
                    with open(TRACE_PATH, "a") as _tf:
                        _tf.write(json.dumps(dict(
                            t=round(time.time(), 3), dev=dev_id, level=lv,
                            x=round(nx, 3), y=round(ny, 3),
                            heading=None if hd is None else round(float(hd), 1),
                            pdr_dx=round(dxp, 3), pdr_dy=round(dyp, 3),
                            steps=(info or {}).get("steps"),
                            source="pdr", moved=True)) + "\n")
                except Exception:
                    pass
        except Exception as e:
            print("[pdr] worker error: %s" % str(e)[:100], flush=True)
        time.sleep(0.5)


def poll_generated():
    """Pick up images the phone's shutter produced and show them here."""
    import urllib.request, ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    while not stop:
        try:
            # Scan for any generated image, not just ones belonging to a
            # currently-live phone. Generation takes ~20s and a phone that stops
            # streaming in that window would otherwise never show its result.
            caps = []
            for cf in glob.glob(os.path.expanduser("~/plans/capture_*.jpg")):
                caps.append((cf, os.path.basename(cf)[8:-4], os.path.getmtime(cf)))
            caps.sort(key=lambda q: -q[2])
            CAPTURES[:] = caps[:24]
            for fp in glob.glob(os.path.expanduser("~/plans/snap_gen_*.png")):
                did = os.path.basename(fp)[9:-4]
                mt = os.path.getmtime(fp)
                cur = GEN_IMG.get(did)
                if cur is None or mt > cur[2]:
                    im = cv2.imread(fp)
                    if im is not None:
                        GEN_IMG[did] = (im, GEN_PRESETS[GEN_SEL[0] % len(GEN_PRESETS)], mt)
                        print("[gen] new image for %s (%dx%d)"
                              % (did, im.shape[1], im.shape[0]), flush=True)
        except Exception:
            pass
        time.sleep(1.5)


def request_generation(dev_id, preset):
    """Ask the bridge to capture + generate for this device."""
    import urllib.request, ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with _plock:
            img = dict(phones["frames"]).get(dev_id)
        if img is not None:
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if ok:
                req = urllib.request.Request(BRIDGE + "/snapshot/" + dev_id,
                                             data=buf.tobytes(), method="POST")
                urllib.request.urlopen(req, timeout=20, context=ctx).read()
        q = urllib.request.Request(
            BRIDGE + "/generate/" + dev_id + "?preset=" +
            urllib.parse.quote(preset), data=b"", method="POST")
        urllib.request.urlopen(q, timeout=20, context=ctx).read()
        print("[gen] requested '%s' for %s" % (preset, dev_id), flush=True)
    except Exception as e:
        print("[gen] request failed: %s" % str(e)[:90], flush=True)


def poll_phones():
    import urllib.request, ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE           # self-signed: getUserMedia needs TLS
    while not stop:
        try:
            with urllib.request.urlopen(BRIDGE + "/devices", timeout=3, context=ctx) as r:
                d = json.loads(r.read().decode())
            live = [x for x in d.get("devices", []) if x.get("live")]
            frames = {}
            for dev in live[:6]:
                try:
                    with urllib.request.urlopen(BRIDGE + "/latest/" + dev["id"],
                                                timeout=3, context=ctx) as r:
                        buf = np.frombuffer(r.read(), np.uint8)
                    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                    if img is not None:
                        frames[dev["id"]] = img
                except Exception:
                    pass
            for dev in d.get("devices", []):
                sv = dev.get("sensor")
                if isinstance(sv, dict) and sv:
                    SENSORS[dev["id"]] = sv
                    h = SENSOR_HIST.setdefault(dev["id"], {})
                    for kk, vv in sv.items():
                        if isinstance(vv, (int, float)):
                            ser = h.setdefault(kk, [])
                            ser.append(float(vv))
                            del ser[:-120]        # ~1 minute at 0.4s polling
            live_ids = {x["id"] for x in live}
            for stale in [k for k in list(phone3d) if k not in live_ids]:
                phone3d.pop(stale, None)
            for store in (DEPTH_VIEW, QR_SEEN, _flow_prev, _pdr, _head_ref):
                for stale in [k for k in list(store) if k not in live_ids]:
                    store.pop(stale, None)
            with _plock:
                phones["list"], phones["url"], phones["frames"] = live, d.get("url", ""), frames
        except Exception:
            with _plock:
                phones["list"] = []
        time.sleep(0.4)


def sensors_for(dev_id):
    """Gravity + heading for a device, or (None, None).

    Sourced from the /devices payload captured by poll_phones: the dedicated
    /sensor/<id> endpoint returns an error for live devices, so relying on it
    meant no orientation data ever reached placement.
    """
    s = SENSORS.get(dev_id) or {}
    if not s:
        return None, None
    gv = None
    g = s.get("gravity") or s.get("accel")
    if isinstance(g, dict):
        gv = [g.get("x", 0.0), g.get("y", 0.0), g.get("z", -9.81)]
    elif isinstance(g, (list, tuple)) and len(g) >= 3:
        gv = list(g[:3])
    elif s.get("gz") is not None:
        gv = [float(s.get("gx", 0.0)), float(s.get("gy", 0.0)),
              float(s.get("gz", -9.81))]
    hd = s.get("heading")
    if hd is None:
        hd = s.get("compass_heading", s.get("alpha"))
    try:
        hd = float(hd) if hd is not None else None
    except Exception:
        hd = None
    return gv, hd


def device_color(dev_id):
    """Deterministic distinct colour per phone, stable across tabs and restarts."""
    h = 0
    for ch in str(dev_id):
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    hsv = np.uint8([[[int((h * 0.6180339887) % 1.0 * 180), 205, 255]]])
    b, g, r = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0]
    return (int(b), int(g), int(r))


# ---------------- reconstruction ----------------
phone3d = {}                 # dev id -> dict(cloud, rgbc, meta, name)
_p3lock = threading.Lock()
REFRESH_S = 0.6
MAX_PHONE_3D = 3             # measured: 3 concurrent phones ~10 fps each
DEPTH_STRIDE = 4


def free_gib():
    try:
        import subprocess
        o = subprocess.run(["free", "-g"], capture_output=True, text=True, timeout=5).stdout
        return int([l for l in o.splitlines() if l.startswith("Mem:")][0].split()[6])
    except Exception:
        return 0


def backproject(img, d):
    """Metric depth -> coloured cloud in device axes (X right, Y up, Z toward user)."""
    Hh, Ww = img.shape[:2]
    if d.shape != (Hh, Ww):
        d = cv2.resize(d, (Ww, Hh), interpolation=cv2.INTER_LINEAR)
    st = DEPTH_STRIDE
    ds = d[::st, ::st]
    cs = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)[::st, ::st]
    h, w = ds.shape
    fx = w / (2.0 * np.tan(np.radians(HFOV_DEG) / 2.0))       # typical phone hFOV
    uu, vv = np.meshgrid(np.arange(w), np.arange(h))
    P = np.stack([(uu - w / 2.0) * ds / fx,
                  -(vv - h / 2.0) * ds / fx, -ds], -1).reshape(-1, 3).astype(np.float32)
    C = cs.reshape(-1, 3).astype(np.uint8)
    ok = np.isfinite(P).all(1) & (ds.reshape(-1) > 0.15) & (ds.reshape(-1) < 25.0)
    return P[ok], C[ok]


_world = None
_world_last = {}
WORLD_EVERY_S = 6.0         # open-vocab costs ~1.9 s/frame; furniture is static,
                            # but 12 s felt like nothing was being found


def _measured_yaw(xs, zz, Ww, fx):
    """Dominant horizontal orientation of an object's own points, in degrees.

    RoomPlan emits oriented bounding boxes; axis-aligned boxes make furniture
    look scattered even when it is placed correctly. PCA on the object's floor
    projection recovers which way it faces. Returned in the camera's frame — the
    caller adds the camera yaw to get a building-frame angle.

    Returns None when the footprint is near-circular, because a plant or a stool
    has no meaningful facing and a random angle is worse than none.
    """
    if len(zz) < 60:
        return None
    X = (xs - Ww / 2.0) * zz / fx
    P = np.stack([X, zz], -1)
    P = P - P.mean(0)
    try:
        w, V = np.linalg.eigh(np.cov(P.T))
    except Exception:
        return None
    order = np.argsort(-w)
    if w[order[1]] <= 1e-9:
        return None
    if w[order[0]] / w[order[1]] < 1.6:      # too round to have a facing
        return None
    v = V[:, order[0]]
    return float(np.degrees(np.arctan2(v[1], v[0])))


def _measured_size(cls, xs, ys, zz, Ww, Hh, fx):
    """Real (w, depth, h) in metres from an object's own pixels.

    Back-projects the segmentation mask rather than the box, so neighbouring
    walls and floor don't inflate the extent. Percentiles instead of min/max
    because monocular depth has long tails. A measurement that disagrees wildly
    with the class prior is rejected — a chair 6 m wide is a depth artefact, not
    a chair.
    """
    if len(zz) < 40:
        return None
    X = (xs - Ww / 2.0) * zz / fx
    Y = -(ys - Hh / 2.0) * zz / fx
    w_m = float(np.percentile(X, 97) - np.percentile(X, 3))
    h_m = float(np.percentile(Y, 97) - np.percentile(Y, 3))
    t_m = float(np.percentile(zz, 97) - np.percentile(zz, 3))
    prior = OBJ_SIZE.get(cls)
    if prior:
        # Tightened to 0.5x..2x: measured extents are good for fully-visible
        # isolated objects (a fridge came out 0.76x0.61x1.50 against a
        # 0.75x0.70x1.80 prior) but wrong for occluded ones — a chair behind a
        # desk measured 0.36 m tall, a truncated person 0.20 m wide.
        for got, want in ((w_m, prior[0]), (h_m, prior[2])):
            if got < 0.45 * want or got > 2.3 * want:
                return None
        # thickness is the least trustworthy axis: depth bleeds at mask edges,
        # which made a flat TV measure 0.31 m deep
        t_m = float(np.clip(t_m, 0.5 * prior[1], 1.8 * prior[1]))
    if not all(np.isfinite([w_m, t_m, h_m])) or min(w_m, h_m) < 0.05:
        return None
    return (max(w_m, 0.05), max(t_m, 0.05), max(h_m, 0.05))


_dev_for_sizes = [""]


def detections_3d(img, d, res):
    """YOLO detections + depth -> [(class, xyz_device, conf, size_or_None)].

    Position uses the object's ground-contact pixel (bottom-centre) and median
    depth: contact localises far better than a box centre, which floats mid-air
    for anything tall. Size is measured per object when a mask is available, so
    primitives are stretched to the real thing instead of a canned per-class box.
    """
    out = []
    if res is None or res.boxes is None or d is None:
        return out
    Hh, Ww = img.shape[:2]
    if d.shape != (Hh, Ww):
        d = cv2.resize(d, (Ww, Hh), interpolation=cv2.INTER_LINEAR)
    fx = Ww / (2.0 * np.tan(np.radians(HFOV_DEG) / 2.0))
    names = res.names if hasattr(res, "names") else {}
    masks = None
    if getattr(res, "masks", None) is not None:
        try:
            masks = res.masks.data.cpu().numpy()
        except Exception:
            masks = None
    for i, b in enumerate(res.boxes):
        cls = names.get(int(b.cls[0]), str(int(b.cls[0])))
        if cls not in scenegraph3d.RELIABLE:
            continue
        conf = float(b.conf[0])
        x1, y1, x2, y2 = [int(v) for v in b.xyxy[0]]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(Ww - 1, x2), min(Hh - 1, y2)
        if x2 <= x1 or y2 <= y1:
            continue

        # An object touching the frame edge is cut off, so its measured extent is
        # a lower bound, not a size. Use the class prior for those instead.
        edges = ((x1 <= 2) + (y1 <= 2) + (x2 >= Ww - 3) + (y2 >= Hh - 3))
        truncated = edges >= 2      # one edge is a normal framing crop, not a cut-off

        size = yaw = None
        if truncated:
            ys = xs = np.empty(0, np.int64)
        elif masks is not None and i < len(masks):
            m = masks[i]
            if m.shape != (Hh, Ww):
                m = cv2.resize(m, (Ww, Hh), interpolation=cv2.INTER_NEAREST)
            ys, xs = np.nonzero(m > 0.5)
        else:                                   # open-vocab has no masks
            ys, xs = np.mgrid[y1:y2:2, x1:x2:2].reshape(2, -1)
        if len(xs):
            zz = d[ys, xs]
            keep = np.isfinite(zz) & (zz > 0.15) & (zz < 25.0)
            xs, ys, zz = xs[keep], ys[keep], zz[keep]
            if len(zz) >= 40:
                size = _measured_size(cls, xs.astype(np.float32),
                                      ys.astype(np.float32), zz, Ww, Hh, fx)
                yaw = _measured_yaw(xs.astype(np.float32), zz, Ww, fx)

        patch = d[y1:y2, x1:x2]
        patch = patch[np.isfinite(patch) & (patch > 0.15) & (patch < 25.0)]
        if patch.size < 12:
            continue
        dz = float(np.median(patch))
        u, v = (x1 + x2) / 2.0, float(y2)          # bottom-centre
        out.append((cls,
                    np.array([(u - Ww / 2.0) * dz / fx,
                              -(v - Hh / 2.0) * dz / fx, -dz], np.float32),
                    conf, size, yaw))
    return out


def open_vocab_detect(img, dev_id):
    """Lazy open-vocabulary pass for classes COCO lacks (desk, whiteboard...)."""
    global _world
    now = time.time()
    if now - _world_last.get(dev_id, 0) < WORLD_EVERY_S or free_gib() < 5:
        return None
    _world_last[dev_id] = now
    try:
        if _world is None:
            print("[sg] loading open-vocabulary detector ...", flush=True)
            # large variant: on a real frame of this building it found 12
            # objects (desk, whiteboard, cabinet, printer, light) vs 4 for -s
            _world = YOLO("yolov8x-worldv2.pt")
            _world.set_classes(scenegraph3d.OPEN_VOCAB)
        return _world.predict(img, conf=0.25, device=DEV, verbose=False)[0]
    except Exception as e:
        print("[sg] open-vocab failed: %s" % str(e).splitlines()[0][:80], flush=True)
        return None


def pdr_step(dev_id, heading_deg):
    """Advance dead reckoning from the phone's motion buffer.

    Returns (dx, dy, info) in metres since the last call, or (0, 0, None).
    Steps are detected from accelerometer peaks and walked along the current
    heading, which is what turns 'I walked down the hall' into actual movement
    on the map instead of a marker pinned to a room centre.
    """
    import urllib.request, ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(BRIDGE + "/motion/" + dev_id, timeout=2,
                                    context=ctx) as r:
            d = json.loads(r.read().decode())
    except Exception:
        return 0.0, 0.0, None
    samples = d.get("motion") if isinstance(d, dict) else None
    if not isinstance(samples, list) or not samples:
        # log the shape we DID get: if PDR never moves, this line says whether
        # the buffer is empty, missing, or shaped differently than expected
        return 0.0, 0.0, dict(error="no motion samples",
                              keys=sorted(d)[:8] if isinstance(d, dict) else None)

    # Rebuild the time axis the detector requires (see module note above).
    if samples and samples[0].get("t") is None:
        base = _PDR_CLOCK.get(dev_id, 0.0)
        tcur = base
        for sm in samples:
            iv = float(sm.get("interval") or 0.0)
            if iv > 1.0:                 # reported in milliseconds
                iv /= 1000.0
            if not (0.0 < iv < 0.5):     # implausible or absent -> 60 Hz
                iv = 1.0 / 60.0
            tcur += iv
            sm["t"] = tcur
        _PDR_CLOCK[dev_id] = tcur

    tr, last_t = _pdr.get(dev_id, (None, 0.0))
    first_look = tr is None
    if tr is None:
        tr = pdr.PDRTracker()
        _pdr[dev_id] = (tr, 0.0)
    if first_look:
        # Adopt the live edge without integrating the backlog behind it.
        newest = 0.0
        for sm in samples:
            try:
                newest = max(newest, float(sm.get("t") or 0.0))
            except (TypeError, ValueError):
                pass
        _pdr[dev_id] = (tr, newest)
        return 0.0, 0.0, dict(steps=0, skipped_backlog=len(samples))
    fresh = [sm for sm in samples if float(sm.get("t", 0.0)) > last_t]
    if not fresh:
        # The endpoint serves a rolling window, so timestamps repeat across
        # calls and everything looks stale after the first read. If the buffer
        # has grown since we last looked, consume the newest slice anyway.
        if len(samples) > 4:
            seen = _PDR_SEEN.get(dev_id, 0)
            new_n = max(0, len(samples) - seen) if len(samples) >= seen else len(samples)
            _PDR_SEEN[dev_id] = len(samples)
            if new_n <= 0:
                return 0.0, 0.0, dict(steps=0, error="no new samples")
            fresh = samples[-min(len(samples), new_n):]
        else:
            return 0.0, 0.0, dict(error="no fresh samples", have=len(samples),
                                  last_t=last_t)
    if heading_deg is not None:
        tr.heading_deg = float(heading_deg)      # trust the fused heading
    x0, y0 = tr.x, tr.y
    out = tr.update(fresh)
    if out and out.get("steps"):
        print("[pdr] %s steps=%s dist=%.2fm heading=%.0f -> d=(%.3f, %.3f)"
              % (dev_id, out.get("steps"), out.get("distance_m", 0.0),
                 tr.heading_deg, tr.x - x0, tr.y - y0), flush=True)
    _pdr[dev_id] = (tr, float(fresh[-1].get("t", last_t)))
    return float(tr.x - x0), float(tr.y - y0), out


def camera_motion(dev_id, img):
    """Frame-to-frame optical flow: how much is the camera actually moving?

    Answers a question no other signal here does — the depth/plan pipeline will
    happily produce a different position estimate for a stationary phone, and
    then the map shows it wandering. Flow measures real image motion, so a still
    camera can be held still on the map.

    Returns (median_px, is_still) or (None, False) when it cannot tell.
    """
    try:
        g = cv2.cvtColor(cv2.resize(img, (160, 120)), cv2.COLOR_BGR2GRAY)
    except Exception:
        return None, False
    prev = _flow_prev.get(dev_id)
    _flow_prev[dev_id] = g
    if prev is None or prev.shape != g.shape:
        return None, False
    try:
        f = cv2.calcOpticalFlowFarneback(prev, g, None, 0.5, 2, 13, 2, 5, 1.1, 0)
        mag = np.linalg.norm(f, axis=2)
        med = float(np.median(mag))
        return med, med < FLOW_STILL_PX
    except Exception:
        return None, False


def build_phone_cloud(dev_id, name, img):
    """Depth -> cloud -> plan-anchored placement -> accumulate into its level."""
    if free_gib() < 4:
        return
    _dev_for_sizes[0] = dev_id
    flow_px, cam_still = camera_motion(dev_id, img)
    d = infer_depth(img)
    if d is None:
        return

    # If the anchor QR is in view, it is a ruler: known printed size against
    # observed pixels and depth gives the scale error outright.
    try:
        if time.time() - CAL["last"] > 1.5:
            CAL["last"] = time.time()
            r = None if CAL["locked"] else qr_calibrate.calibrate(
                img, d, qr_size_m=QR_SIZE_M, hfov_deg=HFOV_DEG)
            if CAL["locked"]:
                # still detect, so the box keeps drawing — just stop re-solving
                _c2, _t2 = qr_calibrate.detect_qr(img)
                if _c2 is not None:
                    QR_SEEN[dev_id] = dict(
                        QR_SEEN.get(dev_id, {}),
                        corners=np.asarray(_c2, np.float32),
                        frame_wh=(img.shape[1], img.shape[0]),
                        usable=True, t=time.time())
            if r:
                _c, _t = qr_calibrate.detect_qr(img)
                if _c is not None:
                    QR_SEEN[dev_id] = dict(
                        corners=np.asarray(_c, np.float32),
                        implied_m=float(r["implied_qr_size_m"]),
                        corrected_m=float(r["implied_qr_size_m"]) * float(CAL["scale_k"]),
                        depth_m=float(r["depth_at_qr_m"]),
                        frame_wh=(img.shape[1], img.shape[0]),
                        usable=bool(r.get("usable")), t=time.time())
            if r and not r.get("usable"):
                print("[qr] SEEN but rejected: %s (px=%.0f depth=%.2fm k=%.3f)"
                      % (r.get("reject", "?"), r.get("qr_pixels", 0),
                         r.get("depth_at_qr_m", 0), r.get("scale_k", 0)),
                      flush=True)
            if r and r.get("usable"):
                _cal_hits.append(r)
                del _cal_hits[:-15]
                # reject physically impossible reads: a phone is ~55-90 deg,
                # so an implied 27.9 deg means the QR was measured badly
                # Widened from 45-110: real reads were landing at ~28 deg and
                # the filter discarded ALL of them, so calibration never reached
                # its sample count and sat in "CALIBRATING" forever. Reject only
                # the physically absurd.
                _cal_hits[:] = [h for h in _cal_hits
                                if 12.0 <= float(h.get("hfov_implied_deg", 0)) <= 150.0]
                # Calibrate on the FIRST usable read. A QR of known size is a
                # complete measurement in one frame; requiring several only
                # averages noise, and it produced a "CALIBRATING" state that
                # never ended when reads were filtered out. Lock immediately,
                # then keep refining the median silently as more arrive.
                if not CAL["locked"] and r and r.get("usable"):
                    CAL.update(scale_k=float(r["scale_k"]), samples=1,
                               hfov_implied=float(r["hfov_implied_deg"]),
                               spread=None, locked=True, shown_m=QR_SIZE_M)
                    qr_calibrate.save(dict(CAL, qr_pattern_m=QR_SIZE_M))
                    print("[qr] LOCKED on first read: k=%.3f (hFOV~%.1f deg)"
                          % (CAL["scale_k"], CAL["hfov_implied"]), flush=True)
                    ax_ = ANCHOR.get("x", SETUP.get("x"))
                    ay_ = ANCHOR.get("y", SETUP.get("y"))
                    ah_ = ANCHOR.get("heading_deg", SETUP.get("heading"))
                    if ax_ is not None and ay_ is not None:
                        _pose_prev[dev_id] = (float(ax_), float(ay_),
                                              float(ah_ or 0.0), time.time(), 0)
                        TRAILS[dev_id] = [(float(ax_), float(ay_),
                                           ANCHOR.get("level")
                                           or SETUP.get("level"))]
                        _pdr.pop(dev_id, None)     # restart dead reckoning here
                        print("[qr] pose reset to anchor (%.2f, %.2f) heading %.0f"
                              % (float(ax_), float(ay_), float(ah_ or 0.0)),
                              flush=True)
                agg = qr_calibrate.accumulate(_cal_hits, min_n=2)
                if agg:
                    CAL.update(scale_k=agg["scale_k"], samples=agg["samples"],
                               hfov_implied=agg["hfov_implied_deg"],
                               spread=agg["scale_k_spread"])
                    # lock once the estimate stops moving; after this the world
                    # has one fixed scale and measurements stay put
                    if True:   # already locked; this only refines the estimate
                        CAL["locked"] = True
                        CAL["shown_m"] = QR_SIZE_M
                        # The QR is the one thing whose position and facing we
                        # know exactly. On lock, snap this device's pose to the
                        # anchor instead of carrying on from a drifted estimate.
                        if ANCHOR.get("x") is not None:
                            _pose_prev[dev_id] = (
                                float(ANCHOR["x"]), float(ANCHOR["y"]),
                                (float(ANCHOR["heading_deg"])
                                 if ANCHOR.get("heading_deg") is not None else None),
                                time.time(), 0)
                            _head_ref.pop(dev_id, None)   # re-seed heading here
                            TRAILS.pop(dev_id, None)      # trail restarts at truth
                            _pdr.pop(dev_id, None)        # dead reckoning restarts
                            print("[qr] pose reset to anchor (%.2f, %.2f) facing %s"
                                  % (ANCHOR["x"], ANCHOR["y"],
                                     ANCHOR.get("heading_deg")), flush=True)
                        print("[qr] LOCKED scale k=%.3f (%d reads, spread %.3f, "
                              "hFOV~%.1f deg)"
                              % (agg["scale_k"], agg["samples"],
                                 agg["scale_k_spread"], agg["hfov_implied_deg"]),
                              flush=True)
                    else:
                        print("[qr] calibrating k=%.3f (%d reads, spread %.3f)"
                              % (agg["scale_k"], agg["samples"],
                                 agg["scale_k_spread"]), flush=True)
                    qr_calibrate.save(dict(CAL, qr_pattern_m=QR_SIZE_M))
    except Exception as e:
        print("[qr] %s" % str(e)[:70], flush=True)

    # one multiplicative correction fixes reconstruction, object sizes and
    # placement together, because it is their product that was wrong
    if CAL["locked"] and 0.35 < CAL["scale_k"] < 3.0:
        d = d * float(CAL["scale_k"])

    # keep a colourised depth map so the operator can see what the geometry is
    # actually built from, not just its result
    try:
        fin = d[np.isfinite(d) & (d > 0)]
        if fin.size:
            lo, hi = np.percentile(fin, 2), np.percentile(fin, 98)
            n = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)
            n[~np.isfinite(d)] = 1.0
            DEPTH_VIEW[dev_id] = cv2.applyColorMap(
                (255 * (1.0 - n)).astype(np.uint8), cv2.COLORMAP_TURBO)
    except Exception:
        pass

    P, C = backproject(img, d)
    if len(P) < 400:
        return
    grav, head = sensors_for(dev_id)

    # Try each storey and keep the better room match — the phone doesn't tell us
    # which floor it is on, but the plan does, by fit.
    best = None
    # If the operator anchored on a storey, only consider that storey. Letting
    # footprint matching pick a level meant the anchor silently never applied
    # whenever it guessed the other one.
    cand_levels = ([ANCHOR["level"]] if ANCHOR.get("x") is not None
                   and ANCHOR.get("level") in LEVELS else list(LEVELS))
    for lv in cand_levels:
        L = LEVELS[lv]
        r = phone_slam.place(P, C, L["base_z"], L["walls"], L["rooms"],
                             gravity=grav, compass_deg=head,
                             room_heights=ROOM_HEIGHTS, level=lv)
        if r is None:
            continue
        r["level"] = lv
        if best is None or (r["room_area"] and abs(np.log(
                max(r["footprint_area"], .1) / max(r["room_area"], .1))) <
                abs(np.log(max(best["footprint_area"], .1) / max(best["room_area"], .1)))):
            best = r
    if best is None:
        return
    if best["confidence"] != "unplaced":
        LEVELS[best["level"]]["acc"].add(best["points"], best["colors"])

    # Where the camera itself is, and which way it looks. The device origin
    # transformed into building coordinates IS the camera position — better than
    # the room centroid — and everything it sees lies in front of it, so the
    # direction to the observed cloud gives heading without needing a compass.
    try:
        cam_pos = phone_slam.apply_transform(
            np.zeros((1, 3), np.float32), best["transform"])[0]
        cen = np.median(best["points"], axis=0)
        fwd = cen[:2] - cam_pos[:2]
        nrm = float(np.linalg.norm(fwd))
        hx, hy, hz = float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2])
        if dev_id not in _pose_prev and ANCHOR["x"] is not None \
                and ANCHOR["level"] == lv:
            # first sighting starts at the anchor the operator clicked
            hx, hy = float(ANCHOR["x"]), float(ANCHOR["y"])
        hd = (float(np.degrees(np.arctan2(fwd[1], fwd[0]))) if nrm > 1e-3 else None)

        # Prefer the phone's measured rotation: anchor the first reading to the
        # facing the operator set, then track its delta. Absolute indoor compass
        # is unreliable, but relative change over a session is usable.
        if head is not None and np.isfinite(head):
            ref = _head_ref.get(dev_id)
            now_h = time.time()
            if ref is not None and now_h - ref[2] > HEAD_REF_STALE_S:
                ref = None            # sensors dropped and returned: re-seed
            if ref is None:
                base_map = (ANCHOR.get("heading_deg")
                            if ANCHOR.get("heading_deg") is not None else (hd or 0.0))
                _head_ref[dev_id] = (float(head), float(base_map), now_h)
                hd = float(base_map)
                print("[heading] %s seeded: phone %.0f deg -> map %.0f deg"
                      % (dev_id, float(head), base_map), flush=True)
            else:
                h0, m0, _t0 = ref
                delta = ((float(head) - h0 + 180.0) % 360.0) - 180.0
                hd = float((m0 + HEADING_SIGN * delta) % 360.0)
                _head_ref[dev_id] = (h0, m0, now_h)
        elif dev_id in _head_ref:
            # no reading this frame: hold the last sensor-derived heading rather
            # than falling back to the snapped geometry, which does not move
            pv_h = _pose_prev.get(dev_id)
            if pv_h is not None and pv_h[2] is not None:
                hd = float(pv_h[2])

        # Every frame re-localizes independently, so the raw estimate jumps
        # around. Exponential smoothing (circular for heading, which wraps at
        # 360) keeps the marker readable without hiding real movement.
        # A stationary camera should not wander on the map. Optical flow says
        # whether the scene actually moved; if it did not, keep the position and
        # let only the heading update.
        pv0 = _pose_prev.get(dev_id)
        dxp, dyp, pinfo = pdr_step(dev_id, hd)
        if pv0 is not None and (abs(dxp) > 1e-4 or abs(dyp) > 1e-4):
            # walked distance beats a room-centroid guess that never moves
            nx_, ny_ = pv0[0] + dxp, pv0[1] + dyp
            # ...and the floor plan constrains it, exactly as snap-to-road
            # constrains a car: you cannot walk through a wall, so a step that
            # crosses one is wrong however confident the estimate was.
            step_len = float(np.hypot(nx_ - pv0[0], ny_ - pv0[1]))
            # Walls do not block movement any more; see the note on
            # WALL_CONSTRAIN. Phones traverse the floor freely.
            blocked = False
            hx, hy = nx_, ny_
            best["pdr"] = pinfo
            best["wall_blocked"] = bool(blocked)
            best["pdr_applied"] = True
        elif cam_still and pv0 is not None and not best.get("pdr_applied"):
            # only hold position when dead reckoning also saw nothing; optical
            # flow alone froze 27 of 64 frames of a real walk
            hx, hy = pv0[0], pv0[1]
        now_t = time.time()
        pv = _pose_prev.get(dev_id)
        if pv is not None:
            px_, py_, ph_, pt_, rej = pv
            dt = max(1e-3, now_t - pt_)
            moved = float(np.hypot(hx - px_, hy - py_))
            turned = (0.0 if (hd is None or ph_ is None)
                      else abs((hd - ph_ + 180) % 360 - 180))
            # A person cannot cross the building between frames. Reject the
            # physically impossible rather than smoothing it in — averaging a
            # teleport just drags the marker halfway across the floor.

            # Reject an implausible POSITION only. Heading comes from the
            # phone's own rotation and is independent — bundling them meant every
            # position rejection froze the arrow, which is why the number moved
            # while the arrow did not.
            moved_bad = moved > MAX_SPEED * dt + 0.5
            if moved_bad and rej < MAX_REJECTS:
                _pose_prev[dev_id] = (px_, py_, hd if hd is not None else ph_,
                                      now_t, rej + 1)
                best["cam_pos"] = [px_, py_, hz]
                best["heading_deg"] = hd if hd is not None else ph_
                best["pose_rejected"] = True
                raise StopIteration          # position held; heading still live
            # Do not average away a measured step. Smoothing suits a noisy
            # vision estimate; dead reckoning already integrates real motion, so
            # damping it to 25% is just throwing away distance the user walked.
            a = 1.0 if best.get("pdr_applied") else POSE_ALPHA
            hx = (1 - a) * px_ + a * hx
            hy = (1 - a) * py_ + a * hy
            if hd is not None and ph_ is not None:
                d0 = np.radians(ph_); d1 = np.radians(hd)
                sx = (1 - a) * np.cos(d0) + a * np.cos(d1)
                sy = (1 - a) * np.sin(d0) + a * np.sin(d1)
                hd = float(np.degrees(np.arctan2(sy, sx)))
            elif hd is None:
                hd = ph_
        hx, hy = clamp_move(dev_id, hx, hy, now_t, best.get("method") or "vision")
        hd = damp_heading(dev_id, hd, now_t)
        _pose_prev[dev_id] = (hx, hy, hd, now_t, 0)
        tr_ = TRAILS.setdefault(dev_id, [])
        if not tr_ or float(np.hypot(hx - tr_[-1][0], hy - tr_[-1][1])) > TRAIL_MIN_STEP:
            tr_.append((hx, hy, lv))
            del tr_[:-TRAIL_MAX]
        best["cam_pos"] = [hx, hy, hz]
        best["heading_deg"] = hd
        best["flow_px"] = None if flow_px is None else round(flow_px, 2)
        best["camera_still"] = bool(cam_still)

        if TRACE_ON[0]:
            try:
                sv_ = SENSORS.get(dev_id) or {}
                rec = dict(
                    t=round(time.time(), 3), dev=dev_id, level=lv,
                    x=round(hx, 3), y=round(hy, 3),
                    heading=None if hd is None else round(hd, 1),
                    method=best.get("method"), conf=best.get("pose_confidence"),
                    room=best.get("room_id"), place_conf=best.get("confidence"),
                    flow_px=best.get("flow_px"), still=bool(cam_still),
                    pdr_dx=round(dxp, 3), pdr_dy=round(dyp, 3),
                    pdr=pinfo, wall_blocked=best.get("wall_blocked"),
                    pdr_applied=bool(best.get("pdr_applied")),
                    pose_rejected=bool(best.get("pose_rejected")),
                    scale_k=CAL["scale_k"], cal_locked=bool(CAL["locked"]),
                    anchor=[ANCHOR.get("x"), ANCHOR.get("y"),
                            ANCHOR.get("heading_deg")],
                    sensor_keys=sorted(sv_.keys())[:14],
                    sensor_heading=sv_.get("heading"),
                    n_landmarks=len(SG.landmarks(lv)))
                with open(TRACE_PATH, "a") as _tf:
                    _tf.write(json.dumps(rec) + "\n")
            except Exception:
                pass
    except StopIteration:
        pass                       # kept the previous pose; nothing more to do
    except Exception as _pe:
        # Report it: silently blanking the pose here looks exactly like dead
        # reckoning failing, and hid the real fault for a long time.
        print("[pose] FAILED for %s: %s: %s"
              % (dev_id, type(_pe).__name__, str(_pe).splitlines()[0][:110]),
              flush=True)
        _pk = _pose_prev.get(dev_id)
        if _pk is not None:
            # keep the last good pose rather than nulling the marker
            best["cam_pos"] = [float(_pk[0]), float(_pk[1]),
                               float(best.get("cam_pos") or [0, 0, 0])[2]
                               if isinstance(best.get("cam_pos"), (list, tuple))
                               else 0.0]
            best["heading_deg"] = _pk[2]
        else:
            best["cam_pos"], best["heading_deg"] = None, None

    with _p3lock:
        phone3d[dev_id] = dict(cloud=best["points"], rgbc=best["colors"],
                               meta=best, name=name)
    _cp = best.get("cam_pos")
    if _cp and (_HANDOFF.get(dev_id) is None
                or abs(_HANDOFF[dev_id][0] - _cp[0]) > 0.25
                or abs(_HANDOFF[dev_id][1] - _cp[1]) > 0.25):
        _HANDOFF[dev_id] = (_cp[0], _cp[1])
        print("[pose] handoff %s -> (%.2f, %.2f)" % (dev_id, _cp[0], _cp[1]),
              flush=True)

    # --- objects into the scenegraph, in the same building frame as the cloud
    if best["confidence"] == "unplaced":
        return                       # an unplaced room would scatter objects
    xf, lv = best["transform"], best["level"]
    obs = []
    try:
        r = seg_model.predict(img, imgsz=IMGSZ, conf=0.30, device=DEV, verbose=False)[0]
        obs += detections_3d(img, d, r)
    except Exception as e:
        print("[sg] seg failed: %s" % str(e).splitlines()[0][:70], flush=True)
    rw = open_vocab_detect(img, dev_id)
    if rw is not None:
        obs += detections_3d(img, d, rw)
    if not obs:
        return
    pts = phone_slam.apply_transform(np.stack([o[1] for o in obs]), xf)

    # Now that objects exist, refine the pose with the strongest signal that
    # applies. Landmarks are camera-frame (x right, y forward) — the same frame
    # detections_3d produces, with depth along -z.
    # scan_match is deliberately NOT fed here: it wants the phone's own levelled
    # observation, and `best["points"]` is already placed in building
    # coordinates, so passing it would localize a cloud against itself.
    try:
        cam_obs = [(c, float(v[0]), float(-v[2])) for c, v, _cf, _sz, _yw in obs]
        _pvv = _pose_prev.get(dev_id)
        _prior = ((_pvv[0], _pvv[1], _pvv[2] if _pvv[2] is not None else 0.0)
                  if _pvv else None)
        _dt = (time.time() - _pvv[3]) if _pvv else None
        cp = best.get("cam_pos")
        sol = localize.solve(
            points=None, walls=LEVELS[lv]["walls"],
            obs_objects=cam_obs, landmarks=SG.landmarks(lv),
            prior=_prior, anchor=ANCHOR, dt=_dt,
            fallback=(dict(x=cp[0], y=cp[1], heading_deg=best.get("heading_deg"))
                      if cp else None))
        if sol and sol.get("x") is not None and sol.get("method") != "footprint":
            best["cam_pos"] = [float(sol["x"]), float(sol["y"]),
                               (cp[2] if cp else LEVELS[lv]["base_z"] + 1.5)]
            if sol.get("heading_deg") is not None:
                best["heading_deg"] = float(sol["heading_deg"])
            _pose_prev[dev_id] = (float(sol["x"]), float(sol["y"]),
                                  best.get("heading_deg"), time.time(), 0)
        best["method"] = (sol or {}).get("method", "footprint")
        best["pose_confidence"] = (sol or {}).get("confidence")
    except Exception as e:
        import traceback
        print("[localize] %s" % str(e)[:90], flush=True)
        traceback.print_exc()

    live_people = {}
    with _sglock:
        for (cls, _, conf, size, oyaw), w in zip(obs, pts):
            # object yaw is measured in the camera frame; the placement yaw
            # rotates it into the building frame
            wyaw = None if oyaw is None else (oyaw + best.get("yaw_deg", 0.0))
            tr = SG.observe(cls, w, conf, lv, size=size, yaw=wyaw)
            if tr is not None and cls == "person" and tr.room_id:
                live_people[tr.room_id] = live_people.get(tr.room_id, 0) + 1
        SG.people_now = live_people
        SG.prune()
        SG.consolidate()


def recon_worker():
    """Continuously re-reconstruct each phone so 3D is a live stream, not a snapshot."""
    last, last_save = {}, -1e9      # negative: force a save on the first pass
    _last_saved = [-1]          # report only when the count actually changes
    _were_live = set()          # devices seen streaming, to detect a stop
    while not stop:
        try:
            with _plock:
                devs, frames = list(phones["list"]), dict(phones["frames"])
            now = time.time()
            # A stream ending is the natural end of a capture, so commit the
            # session then rather than hoping the next periodic tick arrives
            # before the app is closed.
            live_ids = {dv["id"] for dv in devs}
            for gone in (_were_live - live_ids):
                try:
                    with _sglock:
                        _sg = SG.to_dict()
                    _r, _m = scan_session.save(
                        time.strftime("session_%Y%m%d_%H%M%S"), LEVELS,
                        scenegraph=_sg, anchor=dict(ANCHOR),
                        note="stream ended: %s" % gone)
                    made = scan_session.export_all(LEVELS)
                    with _sglock:
                        cnt = {}
                        for tr in SG.tracks:
                            if tr.confirmed:
                                cnt[tr.cls] = cnt.get(tr.cls, 0) + 1
                    SUMMARY.update(active=True, counts=cnt, t=time.time(),
                                   name=_m["name"], points=_m["total_points"])
                    _sid = SESSION.get("db_id")
                    with _sglock:
                        twin_db.save_scenegraph(
                            _sid, twin_db.objects_from_scenegraph(SG))
                        twin_db.save_counts(
                            _sid, twin_db.counts_from_scenegraph(SG))
                    twin_db.end_session(_sid, points=_m["total_points"],
                                        summary=_m)
                    SESSION.update(started=False, db_id=None)
                    print("[scan] %s stopped -> saved %s (%s points), %d exports, "
                          "%d object types%s"
                          % (gone, _m["name"], "{:,}".format(_m["total_points"]),
                             len(made), len(cnt),
                             ("  db session %s" % _sid) if _sid else ""),
                          flush=True)
                except Exception as e:
                    print("[scan] end-of-stream save failed: %s" % str(e)[:80],
                          flush=True)
            _were_live.clear()
            _were_live.update(live_ids)

            if devs and not SESSION["started"]:
                SESSION.update(started=True, t0=time.time(),
                               db_id=twin_db.start_session(
                                   LOCK_LEVEL[0] or ANCHOR.get("level")
                                   or SETUP.get("level"),
                                   [dv["id"] for dv in devs],
                                   CAL["scale_k"]))
                print("[session] capture started", flush=True)
            for dv in devs[:MAX_PHONE_3D]:
                img = frames.get(dv["id"])
                if img is None or now - last.get(dv["id"], 0) < REFRESH_S:
                    continue
                build_phone_cloud(dv["id"], dv["name"], img)
                last[dv["id"]] = time.time()
            # persist the tree so downstream tools (interior generation) can read
            # a finished graph without the app running
            if now - last_save > 10.0:
                last_save = now
                with _sglock:
                    if SG.tracks:
                        SG.save(SG_PATH)
                # a walkthrough must survive the app closing, or it is not a twin
                try:
                    # Unconditional. This was gated first on objects existing and
                    # then on a point threshold; both meant a walkthrough could
                    # end with nothing on disk. A save that only runs when things
                    # are going well is not a save.
                    with _sglock:
                        _sg = SG.to_dict()
                    _pts = sum(len(L["acc"]) for L in LEVELS.values())
                    _r, _m = scan_session.save("autosave", LEVELS, scenegraph=_sg,
                                               anchor=dict(ANCHOR), note="periodic")
                    if _pts != _last_saved[0]:
                        _last_saved[0] = _pts
                        print("[scan] autosaved %s points, %d objects"
                              % ("{:,}".format(_pts), _m.get("objects", 0)),
                              flush=True)
                except Exception as e:
                    print("[scan] autosave: %s" % str(e)[:70], flush=True)
                # measured ceiling heights, from whatever has been walked so far
                try:
                    heights = {}
                    for lv, L in LEVELS.items():
                        P, _c = L["acc"].get()
                        if P is None:
                            continue
                        heights.update(ceiling.measure(P, L["rooms"],
                                                       L["base_z"], level=lv))
                    if heights:
                        ceiling.save(heights, assumed=round(CEIL, 2))
                except Exception as e:
                    print("[ceiling] %s" % str(e)[:70], flush=True)
        except Exception as e:
            print("[recon] %s" % str(e)[:90], flush=True)
        time.sleep(0.15)


# ---------------- projection ----------------
def rot(yaw, pitch):
    a, b = np.radians(yaw), np.radians(pitch)
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]], np.float32)
    Rx = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]], np.float32)
    return Rx @ Rz


def project(P, cols, R, cam, f, cen, ow, oh):
    q = (P - cen) @ R.T + cam
    m = q[:, 2] > 0.25
    q, cols = q[m], cols[m]
    u = (f * q[:, 0] / q[:, 2] + ow / 2).astype(np.int32)
    v = (oh / 2 - f * q[:, 1] / q[:, 2]).astype(np.int32)
    ok = (u >= 0) & (u < ow) & (v >= 0) & (v < oh)
    return u[ok], v[ok], q[ok, 2], cols[ok]


def splat(img, u, v, col, rad, ow, oh):
    if rad <= 1:
        img[v, u] = col; return
    for dy in range(-rad + 1, rad):
        for dx in range(-rad + 1, rad):
            img[np.clip(v + dy, 0, oh - 1), np.clip(u + dx, 0, ow - 1)] = col


def unproject_floor(px, py, level):
    """Screen point in the building pane -> world (x, y) on that storey's floor.

    Inverts the same projection the renderer uses and intersects the ray with the
    floor plane, so clicking the map gives a real building coordinate — which is
    how you tell the app where a QR (or a phone) physically is.
    """
    bw, bh = W - LEFT_W, H - HEADER_H
    u, v = px - LEFT_W, py - HEADER_H
    R = rot(view["yaw"], view["pitch"])
    dist = view["dist"]
    f = bw / 1.5
    dx, dy = (u - bw / 2.0) / f, (bh / 2.0 - v) / f
    target_z = LEVELS[level]["base_z"]
    denom = dx * R[0, 2] + dy * R[1, 2] + R[2, 2]
    if abs(denom) < 1e-6:
        return None
    t = (target_z - BCEN[2] + dist * R[2, 2]) / denom
    if t <= 0:
        return None
    q = np.array([dx * t, dy * t, t], np.float32) - np.array([0, 0, dist], np.float32)
    P = q @ R + BCEN
    return float(P[0]), float(P[1])


def set_anchor(px, py):
    """Right-click drops the join anchor at a real map position."""
    lv = view["levels"] if view["levels"] in LEVELS else "level1"
    got = unproject_floor(px, py, lv)
    if got is None:
        return
    x, y = got
    ANCHOR.update(x=x, y=y, level=lv, set_at=time.time())
    try:
        import anchors as _anch
        d = _anch.load()
        d["anchors"] = [a for a in d["anchors"] if a.get("id") != "manual"]
        walls = LEVELS[lv]["walls"]
        d["anchors"].append(dict(
            id="manual", label="QR start (clicked)", room_id="", level=lv,
            x=round(x, 2), y=round(y, 2),
            z=round(LEVELS[lv]["base_z"], 2),
            heading_deg=round(_anch.facing_nearest_wall(x, y, walls), 1),
            url="", placement="set by clicking the map on the Spark"))
        _anch.save(d)
    except Exception as e:
        print("[anchor] not persisted: %s" % str(e)[:70], flush=True)
    print("[anchor] set at (%.2f, %.2f) on %s" % (x, y, lv), flush=True)


def render_setup(canvas):
    """Startup gate: pick the floor, then click where the join QR is posted.

    Localizing from geometry alone was measured failing in this building (median
    2.49 m, heading flipping 180 deg in corridors), so the run starts from a
    known point instead. One click removes the ambiguity that no amount of
    matching can resolve.
    """
    lv = SETUP["level"]
    L = LEVELS.get(lv)
    canvas[:] = 16
    cv2.rectangle(canvas, (0, 0), (W, 78), (26, 26, 32), -1)
    cv2.putText(canvas, "SET UP  -  where is the QR code posted?", (26, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.82, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(canvas, "[1]/[2] floor   click = place QR   [<]/[>] rotate facing"
                "   [r] reset position   [enter] start   [q] quit", (26, 62),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (160, 200, 235), 1, cv2.LINE_AA)
    if L is None:
        return

    # plain top-down plan: the easiest thing to click accurately
    w = L["walls"]
    x0 = min(w[:, 0].min(), w[:, 2].min()); x1 = max(w[:, 0].max(), w[:, 2].max())
    y0 = min(w[:, 1].min(), w[:, 3].min()); y1 = max(w[:, 1].max(), w[:, 3].max())
    pad = 40
    sx = (W - 2 * pad) / max(x1 - x0, 1e-6)
    sy = (H - 78 - 2 * pad) / max(y1 - y0, 1e-6)
    s = min(sx, sy)
    ox = pad + (W - 2 * pad - (x1 - x0) * s) / 2.0
    oy = 78 + pad + (H - 78 - 2 * pad - (y1 - y0) * s) / 2.0
    # y1 - y, not y - y0: the 3D view puts +y up the screen, and a setup map that
    # ran +y down showed the plan mirrored against the model you then look at.
    SETUP["map"] = (x0, y1, s, ox, oy)

    def _px(px_, py_):
        return int((px_ - x0) * s + ox), int((y1 - py_) * s + oy)

    for a, b, c, d in w:
        cv2.line(canvas, _px(a, b), _px(c, d),
                 (120, 170, 210) if lv == "level1" else (210, 170, 120), 1)
    for r in L["rooms"][:400]:
        P = np.asarray(r["poly"], np.float32)
        q = np.array([_px(px_, py_) for px_, py_ in P], np.int32)
        cv2.polylines(canvas, [q], True, (60, 70, 80), 1)

    cv2.putText(canvas, "%s   %d rooms   %.0f x %.0f m"
                % (lv, len(L["rooms"]), x1 - x0, y1 - y0), (26, H - 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (130, 130, 130), 1)

    if SETUP["x"] is not None and SETUP["level"] == lv:
        px = _px(SETUP["x"], SETUP["y"])
        cv2.drawMarker(canvas, px, (80, 255, 180), cv2.MARKER_CROSS, 26, 2)
        cv2.circle(canvas, px, 16, (80, 255, 180), 2)
        hd = SETUP["heading"]
        if hd is not None:
            a = np.radians(hd)
            # compass: x = sin, y = cos
            tip = _px(SETUP["x"] + 4.0 * np.sin(a), SETUP["y"] + 4.0 * np.cos(a))
            cv2.arrowedLine(canvas, px, tip, (80, 255, 180), 3, cv2.LINE_AA,
                            tipLength=0.25)
        cv2.putText(canvas, "QR (%.1f, %.1f)%s" % (
            SETUP["x"], SETUP["y"],
            "" if hd is None else "  facing %.0f deg" % (hd % 360)),
            (px[0] + 20, px[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
            (80, 255, 180), 1)
        cv2.putText(canvas, "[<] [>] set which way a reader faces, then [enter]",
                    (W // 2 - 250, H - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                    (120, 255, 190), 2, cv2.LINE_AA)


def setup_click(x, y):
    """Map a click on the setup plan back to building coordinates."""
    if not SETUP["map"] or y < 78:
        return
    x0, y1, s, ox, oy = SETUP["map"]
    SETUP["x"] = float((x - ox) / s + x0)
    SETUP["y"] = float(y1 - (y - oy) / s)
    try:
        import anchors as _a
        SETUP["heading"] = float(_a.facing_nearest_wall(
            SETUP["x"], SETUP["y"], LEVELS[SETUP["level"]]["walls"]))
    except Exception:
        SETUP["heading"] = 0.0
    print("[setup] QR at (%.2f, %.2f) on %s"
          % (SETUP["x"], SETUP["y"], SETUP["level"]), flush=True)


def on_mouse(e, x, y, flags, _):
    if SETUP["active"]:
        if e == cv2.EVENT_LBUTTONDOWN:
            setup_click(x, y)
        return
    # single view: dragging orbits only over the building, so clicks on the
    # phone column never yank the camera
    if y >= HEADER_H and x < LEFT_W and e == cv2.EVENT_LBUTTONDOWN:
        if panel_click(x, y):
            return
    if y < HEADER_H or x < LEFT_W:
        if e == cv2.EVENT_LBUTTONUP:
            view["drag"] = False
        return
    if e == cv2.EVENT_RBUTTONDOWN:
        set_anchor(x, y)
        return
    if e == cv2.EVENT_LBUTTONDOWN:
        view.update(drag=True, mx=x, my=y)
    elif e == cv2.EVENT_LBUTTONUP:
        view["drag"] = False
    elif e == cv2.EVENT_MOUSEMOVE and view["drag"]:
        view["yaw"] += (x - view["mx"]) * 0.4
        view["pitch"] = float(np.clip(view["pitch"] - (y - view["my"]) * 0.3, 3, 89))
        view.update(mx=x, my=y)
    elif e == cv2.EVENT_MOUSEWHEEL and view["dist"]:
        view["dist"] = float(np.clip(view["dist"] * (1 / 1.15 if flags > 0 else 1.15),
                                     2.0, 400.0))


# ---------------- tabs ----------------
def tab_live(canvas, dets):
    with _plock:
        devs, frames = list(phones["list"]), dict(phones["frames"])
    if not devs:
        cv2.putText(canvas, "no phones connected", (W // 2 - 190, H // 2 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (90, 90, 90), 2, cv2.LINE_AA)
        cv2.putText(canvas, "scan the QR (bottom right) to add one",
                    (W // 2 - 200, H // 2 + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (80, 80, 80), 1, cv2.LINE_AA)
        return
    n = len(devs)
    cols = 1 if n == 1 else (2 if n <= 4 else 3)
    rows = int(np.ceil(n / cols))
    cw, ch = W // cols, (H - TAB_H) // rows
    for i, dv in enumerate(devs):
        pane = np.full((ch, cw, 3), 20, np.uint8)
        img = frames.get(dv["id"])
        cnt = 0
        if img is not None:
            pane = cv2.resize(img, (cw, ch))
            res = dets.get(dv["id"])
            if res is not None:
                sx, sy = cw / img.shape[1], ch / img.shape[0]
                for b in res.boxes:
                    x1, y1, x2, y2 = map(int, b.xyxy[0])
                    cv2.rectangle(pane, (int(x1 * sx), int(y1 * sy)),
                                  (int(x2 * sx), int(y2 * sy)), (0, 255, 0), 2)
                    cnt += 1
        else:
            cv2.putText(pane, "no signal", (cw // 2 - 60, ch // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 80, 80), 2, cv2.LINE_AA)
        col = device_color(dv["id"])
        cv2.rectangle(pane, (0, 0), (cw, 28), (0, 0, 0), -1)
        cv2.putText(pane, "[PHONE] %s   people %d" % (dv["name"], cnt), (8, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2, cv2.LINE_AA)
        cv2.rectangle(pane, (0, 0), (cw - 1, ch - 1), col, 2)
        r_, c_ = divmod(i, cols)
        canvas[TAB_H + r_ * ch:TAB_H + (r_ + 1) * ch, c_ * cw:(c_ + 1) * cw] = pane


def tab_rooms(canvas, dets):
    with _p3lock:
        p3 = {k: dict(v) for k, v in phone3d.items()}
    if not p3:
        cv2.putText(canvas, "no reconstructions yet", (W // 2 - 200, H // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (90, 90, 90), 2, cv2.LINE_AA)
        return
    n = len(p3)
    cols = 1 if n == 1 else (2 if n <= 4 else 3)
    rows = int(np.ceil(n / cols))
    cw, ch = W // cols, (H - TAB_H) // rows
    R = rot(view["yaw"], view["pitch"])
    f = cw / 1.5
    for i, (pid, pc) in enumerate(p3.items()):
        pane = np.full((ch, cw, 3), 14, np.uint8)
        cl = pc["cloud"]
        cen = np.median(cl, axis=0)
        spread = np.percentile(np.abs(cl - cen), 85, axis=0)
        d = float(max(1.5, 2.6 * float(np.linalg.norm(spread))))
        cam = np.array([0, 0, d], np.float32)
        rad = int(np.clip(round(2.0 * 10.0 / d), 1, 4))
        u, v, z, col = project(cl, pc["rgbc"], R, cam, f, cen, cw, ch)
        o = np.argsort(-z)
        splat(pane, u[o], v[o], col[o][:, ::-1], rad, cw, ch)
        m = pc["meta"]
        pcolr = device_color(pid)
        cv2.rectangle(pane, (0, 0), (cw, 46), (0, 0, 0), -1)
        cv2.putText(pane, "[PHONE] %s" % pc["name"], (8, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, pcolr, 2, cv2.LINE_AA)
        cv2.putText(pane, "%s  %s  %.0f m2  yaw %.0f" % (
            m.get("room_id") or "unplaced", m.get("confidence", "?"),
            m.get("footprint_area", 0), m.get("yaw_deg", 0)),
            (8, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (170, 170, 170), 1)
        cv2.rectangle(pane, (0, 0), (cw - 1, ch - 1), pcolr, 2)
        r_, c_ = divmod(i, cols)
        canvas[TAB_H + r_ * ch:TAB_H + (r_ + 1) * ch, c_ * cw:(c_ + 1) * cw] = pane


def tab_building(canvas, dets):
    ch = H - TAB_H
    pane = np.full((ch, W, 3), 14, np.uint8)
    R = rot(view["yaw"], view["pitch"])
    cam = np.array([0, 0, view["dist"]], np.float32)
    f = W / 1.5
    rad = int(np.clip(round(1.6 * SPAN / max(view["dist"], 1e-3)), 1, 5))

    # accumulated phone geometry — the "filled in" part
    filled = 0
    for lv, L in LEVELS.items():
        P, C = L["acc"].get()
        if P is None:
            continue
        filled += len(P)
        u, v, z, col = project(P, C, R, cam, f, BCEN, W, ch)
        o = np.argsort(-z)
        splat(pane, u[o], v[o], col[o][:, ::-1], rad, W, ch)

    # ONE depth-sorted pass over every surface — floors, walls, objects, both
    # storeys. Drawing each level in its own pass meant level 2 was always
    # painted last and level 1 bled through it regardless of what was in front.
    Q, C = [], []
    shown = [lv for lv in LEVELS if view["levels"] in ("both", lv)]
    for lv in shown:
        L = LEVELS[lv]
        Q.append(L["quads"]); C.append(L["qcols"])
    oq, oc, nobj = object_primitives(R, cam, f, W, ch)
    if oq is not None:
        Q.append(oq); C.append(oc)
    plan_render.draw_walls(pane, np.concatenate(Q), np.concatenate(C),
                           R, cam, f, BCEN, W, ch, edge=True)

    # live phone markers
    with _p3lock:
        p3 = {k: dict(v) for k, v in phone3d.items()}
    for pid, pc in p3.items():
        m = pc["meta"]
        if not m.get("centroid"):
            continue
        pos = np.array([m["centroid"][0], m["centroid"][1],
                        LEVELS[m["level"]]["base_z"] + 1.5], np.float32)
        q = (pos - BCEN) @ R.T + cam
        if q[2] > 0.25:
            px = (int(f * q[0] / q[2] + W / 2), int(ch / 2 - f * q[1] / q[2]))
            cv2.circle(pane, px, 8, device_color(pid), -1)
            cv2.circle(pane, px, 11, (255, 255, 255), 1)
            cv2.putText(pane, "%s -> %s" % (pc["name"], m.get("room_id") or "unplaced"),
                        (px[0] + 14, px[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        device_color(pid), 1, cv2.LINE_AA)

    rooms_hit = len({p["meta"].get("room_id") for p in p3.values()
                     if p["meta"].get("room_id")})
    cv2.rectangle(pane, (0, 0), (W, 46), (0, 0, 0), -1)
    cv2.putText(pane, "1700 Westlake Ave N   [f] floors: %s   filled %s pts   objects %d   rooms mapped %d"
                % (view["levels"], "{:,}".format(filled), nobj, rooms_hit), (10, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 2)
    cv2.putText(pane, "plan = survey truth; fill = phone depth anchored to rooms (not bundle-adjusted)",
                (10, 39), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (140, 140, 140), 1, cv2.LINE_AA)

    # scenegraph tree overlaid here rather than on its own tab — an empty tab
    # tells you nothing, and the tree only makes sense beside the geometry
    with _sglock:
        lines, cnt = SG.tree_lines(max_rooms=9), SG.counts()
    pw, phh = 430, min(ch - 70, 34 + 20 * (len(lines) + 2))
    x0, y0 = 12, 58
    sub = pane[y0:y0 + phh, x0:x0 + pw]
    cv2.addWeighted(sub, 0.22, np.zeros_like(sub), 0.78, 0, sub)
    cv2.rectangle(pane, (x0, y0), (x0 + pw, y0 + phh), (85, 85, 92), 1)
    cv2.putText(pane, "SCENEGRAPH   %d objects  %d rooms  %d people"
                % (cnt["confirmed"], cnt["rooms_with_objects"], cnt["people"]),
                (x0 + 10, y0 + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (150, 210, 255), 1, cv2.LINE_AA)
    yy = y0 + 44
    for ln in lines:
        if yy > y0 + phh - 8:
            break
        col = (150, 210, 255) if ln.startswith("|  +-") else (
            (255, 205, 140) if ln.startswith("+-") else (185, 185, 185))
        cv2.putText(pane, ln[:56], (x0 + 10, yy), cv2.FONT_HERSHEY_SIMPLEX, 0.40, col, 1, cv2.LINE_AA)
        yy += 20
    canvas[TAB_H:] = pane


OBJ_COL = {"person": (60, 255, 255), "chair": (120, 230, 120), "desk": (255, 190, 90),
           "whiteboard": (240, 240, 240), "laptop": (200, 160, 255),
           "couch": (140, 200, 255), "potted plant": (110, 240, 160)}
DEF_COL = (180, 180, 190)

# real-world footprints so primitives read at true scale (w, d, height) metres
OBJ_SIZE = {
    "person": (0.50, 0.40, 1.70), "chair": (0.55, 0.55, 0.90),
    "desk": (1.40, 0.70, 0.75), "dining table": (1.60, 0.90, 0.75),
    "couch": (2.00, 0.90, 0.80), "bed": (2.00, 1.50, 0.55),
    "tv": (1.10, 0.08, 0.65), "whiteboard": (1.80, 0.08, 1.20),
    "laptop": (0.35, 0.25, 0.25), "keyboard": (0.45, 0.15, 0.03),
    "mouse": (0.12, 0.07, 0.04), "book": (0.22, 0.15, 0.05),
    "potted plant": (0.45, 0.45, 0.80), "cabinet": (1.00, 0.50, 1.10),
    "shelf": (1.00, 0.35, 1.60), "refrigerator": (0.75, 0.70, 1.80),
    "microwave": (0.50, 0.38, 0.30), "oven": (0.60, 0.60, 0.85),
    "toaster": (0.28, 0.18, 0.20), "sink": (0.55, 0.45, 0.25),
    "toilet": (0.40, 0.65, 0.75), "bench": (1.50, 0.45, 0.45),
    "air vent": (0.60, 0.15, 0.30), "clock": (0.30, 0.05, 0.30),
    "vase": (0.20, 0.20, 0.35), "bottle": (0.08, 0.08, 0.25),
    "cup": (0.09, 0.09, 0.11), "bowl": (0.16, 0.16, 0.08),
    "backpack": (0.32, 0.20, 0.45), "handbag": (0.30, 0.15, 0.25),
    "suitcase": (0.45, 0.25, 0.65), "cell phone": (0.08, 0.01, 0.15),
    "conference table": (2.40, 1.10, 0.75), "office chair": (0.60, 0.60, 1.05),
    "computer monitor": (0.55, 0.18, 0.42), "printer": (0.50, 0.45, 0.35),
    "filing cabinet": (0.45, 0.60, 1.30), "bookshelf": (0.90, 0.32, 1.80),
    "ceiling light": (0.60, 0.20, 0.15), "floor lamp": (0.35, 0.35, 1.60),
    "coffee machine": (0.30, 0.35, 0.45), "water cooler": (0.35, 0.35, 1.20),
    "trash can": (0.35, 0.35, 0.60), "projector screen": (2.00, 0.06, 1.50),
    "coat rack": (0.45, 0.45, 1.70), "stool": (0.38, 0.38, 0.65),
    "partition wall": (1.60, 0.08, 1.60), "door": (0.90, 0.06, 2.05),
    "window": (1.20, 0.06, 1.40), "wall art": (0.70, 0.04, 0.55),
    "picture frame": (0.45, 0.04, 0.35), "exit sign": (0.35, 0.06, 0.18),
    "fire extinguisher": (0.20, 0.20, 0.55), "thermostat": (0.12, 0.03, 0.12),
    "light switch": (0.09, 0.02, 0.12), "power outlet": (0.08, 0.02, 0.12),
}


def object_primitives(R, cam, f, ow, oh):
    """Confirmed scenegraph objects as shaded 3D boxes. Returns (quads, cols, n)."""
    with _sglock:
        tracks = [(t.cls, t.pos.copy(),
                   None if t.size is None else t.size.copy(), t.yaw)
                  for t in SG.tracks if t.confirmed]
    if not tracks:
        return None, None, 0
    MESHES = plan_render.load_meshes()
    Q, C = [], []
    for cls, pos, msize, myaw in tracks:
        # stretch the primitive to the object's measured extent; the per-class
        # prior is only the fallback when nothing plausible was measured
        size = tuple(msize) if msize is not None else OBJ_SIZE.get(cls, (0.40, 0.40, 0.40))
        # real object shape when we have a usable mesh; a box only as fallback
        q = plan_render.mesh_quads(cls, pos, size, yaw_deg=(myaw or 0.0),
                                   meshes=MESHES)
        if q is None or not len(q):
            q = plan_render.box_quads(pos, size, yaw_deg=(myaw or 0.0))
            C.append(plan_render.shade_faces(OBJ_COL.get(cls, DEF_COL), len(q)))
        else:
            base = np.asarray(OBJ_COL.get(cls, DEF_COL), np.float32)
            # shade per-face by facing so a mesh reads as solid, not a silhouette
            nrm = np.cross(q[:, 1] - q[:, 0], q[:, 2] - q[:, 0])
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            up = np.abs(nrm[:, 2:3] / np.maximum(ln, 1e-6))
            C.append((base[None, :] * (0.55 + 0.45 * up)).astype(np.uint8))
        Q.append(q)
    return np.concatenate(Q), np.concatenate(C), len(tracks)


def tab_scenegraph(canvas, dets):
    """The tree as it builds — plan gives the branches, video gives the leaves."""
    ch = H - TAB_H
    pane = np.full((ch, W, 3), 16, np.uint8)
    with _sglock:
        lines = SG.tree_lines(max_rooms=16)
        cnt = SG.counts()

    cv2.putText(pane, "3D SCENEGRAPH", (40, 46), cv2.FONT_HERSHEY_SIMPLEX,
                0.95, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(pane, "confirmed objects %d   tracks %d   rooms mapped %d   people %d"
                % (cnt["confirmed"], cnt["tracks"], cnt["rooms_with_objects"],
                   cnt["people"]), (40, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (150, 200, 255), 1, cv2.LINE_AA)

    y = 116
    for ln in lines[:26]:
        col = (235, 235, 235)
        if ln.startswith("|  +-"):
            col = (150, 210, 255)
        elif ln.strip().startswith("- "):
            col = (165, 165, 165)
        elif ln.startswith("+-"):
            col = (255, 205, 140)
        cv2.putText(pane, ln[:96], (44, y), cv2.FONT_HERSHEY_SIMPLEX, 0.46, col, 1, cv2.LINE_AA)
        y += 23
        if y > ch - 60:
            break

    cv2.putText(pane, "objects appear after %d sightings; people are counted, not tracked"
                % scenegraph3d.MIN_OBS, (44, ch - 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (120, 120, 120), 1, cv2.LINE_AA)
    canvas[TAB_H:] = pane


HEADER_H = 40
LEFT_W = max(360, int(W * 0.50))   # phone column: the live feed is the thing
                                   # the operator watches, so give it real space


def panel_phones(canvas, dets):
    """Left column: each phone's live 2D feed above its own 3D reconstruction."""
    with _plock:
        devs, frames = list(phones["list"]), dict(phones["frames"])
    with _p3lock:
        p3 = {k: dict(v) for k, v in phone3d.items()}

    y = HEADER_H
    avail = H - HEADER_H
    if not devs:
        cv2.putText(canvas, "no phones connected", (18, y + 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (110, 110, 110), 1, cv2.LINE_AA)
        cv2.putText(canvas, "scan the QR to join", (18, y + 66),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (85, 85, 85), 1, cv2.LINE_AA)
        VIDEO_BOTTOM[0] = HEADER_H + 90
        return
    n = min(len(devs), 3)
    rh = avail // n
    # A single phone gets the lion's share: the feed is what is being watched.
    tile_h = int(rh * (0.72 if n == 1 else 0.56))
    VIDEO_BOTTOM[0] = HEADER_H + (n - 1) * rh + tile_h + 30
    R = rot(view["yaw"], view["pitch"])

    for i, dv in enumerate(devs[:n]):
        top = y + i * rh
        col = device_color(dv["id"])
        img = frames.get(dv["id"])
        # --- live 2D
        cell = np.full((tile_h, LEFT_W - 16, 3), 22, np.uint8)
        mode = VIEW_MODE[0]
        src = img
        if mode == "depth":
            src = DEPTH_VIEW.get(dv["id"], img)
        _lb = None      # (scale, off_x, off_y) shared with the QR overlay
        if src is not None:
            _tw, _th = LEFT_W - 16, tile_h
            _sh, _sw = src.shape[:2]
            _sc = min(_tw / float(_sw), _th / float(_sh))
            _nw, _nh = max(1, int(round(_sw * _sc))), max(1, int(round(_sh * _sc)))
            _ox, _oy = (_tw - _nw) // 2, (_th - _nh) // 2
            cell = np.full((_th, _tw, 3), 22, np.uint8)
            cell[_oy:_oy + _nh, _ox:_ox + _nw] = cv2.resize(src, (_nw, _nh))
            _lb = (_sc, _ox, _oy)
        if mode == "points":
            # the reconstruction itself, from this device's own viewpoint
            cell = np.full((tile_h, LEFT_W - 16, 3), 12, np.uint8)
            pc = p3.get(dv["id"])
            if pc is not None and pc.get("cloud") is not None and len(pc["cloud"]):
                cl = pc["cloud"]
                cen = np.median(cl, axis=0)
                spread = np.percentile(np.abs(cl - cen), 85, axis=0)
                dd = float(max(1.5, 2.6 * float(np.linalg.norm(spread))))
                uu, vv, zz, cc2 = project(cl, pc["rgbc"], rot(view["yaw"], view["pitch"]),
                                          np.array([0, 0, dd], np.float32),
                                          (LEFT_W - 16) / 1.5, cen, LEFT_W - 16, tile_h)
                o2 = np.argsort(-zz)
                splat(cell, uu[o2], vv[o2], cc2[o2][:, ::-1], 2, LEFT_W - 16, tile_h)
        if src is not None or mode == "points":
            res = dets.get(dv["id"]) if mode == "video" else None
            if res is not None and res.boxes is not None and img is not None:
                sx, sy = (LEFT_W - 16) / img.shape[1], tile_h / img.shape[0]
                nm = res.names if hasattr(res, "names") else {}
                for b in res.boxes:
                    cls = nm.get(int(b.cls[0]), "")
                    if cls not in scenegraph3d.RELIABLE:
                        continue          # only draw what the scenegraph trusts
                    if float(b.conf[0]) < scenegraph3d.RELIABLE[cls][0]:
                        continue
                    x1, y1_, x2, y2 = map(int, b.xyxy[0])
                    bc = (60, 255, 255) if cls == "person" else (120, 235, 120)
                    cv2.rectangle(cell, (int(x1 * sx), int(y1_ * sy)),
                                  (int(x2 * sx), int(y2 * sy)), bc, 2)
                    cv2.putText(cell, cls, (int(x1 * sx) + 2, max(11, int(y1_ * sy) - 4)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.36, bc, 1)
        else:
            cv2.putText(cell, "no signal", (12, tile_h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (80, 80, 80), 1, cv2.LINE_AA)
        # QR box with its measured size — the calibration made checkable rather
        # than a number to be trusted
        qs = QR_SEEN.get(dv["id"])
        if img is not None and qs and time.time() - qs["t"] < 6.0:
            fw, fh = qs["frame_wh"]
            if _lb is not None:
                _s, _ox2, _oy2 = _lb          # match the letterboxed picture
            else:
                _s = min((LEFT_W - 16) / float(fw), tile_h / float(fh))
                _ox2 = _oy2 = 0
            pts = np.stack([qs["corners"][:, 0] * _s + _ox2,
                            qs["corners"][:, 1] * _s + _oy2], -1).astype(np.int32)
            # red until the scale is actually solved, green after: the operator
            # can see calibration state from across the room
            calibrated = bool(CAL["locked"])
            qcol = (90, 235, 90) if calibrated else (60, 60, 235)
            cv2.polylines(cell, [pts], True, qcol, 2, cv2.LINE_AA)
            # once locked the measurement is fixed; before that it is a live
            # reading and visibly labelled as such
            m_ = (CAL["shown_m"] or QR_SIZE_M) if calibrated else qs.get("implied_m", 0.0)
            lbl = "%.1f cm / %.1f in%s" % (m_ * 100.0, m_ * 39.3701,
                                           "  LOCKED" if calibrated else "  CALIBRATING")
            ly = max(12, int(pts[:, 1].min()) - 6)
            cv2.putText(cell, lbl, (int(pts[:, 0].min()), ly),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, qcol, 1, cv2.LINE_AA)
            cv2.putText(cell, "true %.1f cm  @ %.2f m" % (QR_SIZE_M * 100.0,
                                                          qs["depth_m"]),
                        (int(pts[:, 0].min()), min(tile_h - 6, ly + 16)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.36, qcol, 1, cv2.LINE_AA)

        # A phone can stream video while sending no motion at all; without
        # saying so, "the position will not move" looks like a tracking bug.
        _sv = SENSORS.get(dv["id"]) or {}
        if not [k for k in _sv if isinstance(_sv[k], (int, float))]:
            cv2.rectangle(cell, (6, tile_h - 30), (LEFT_W - 28, tile_h - 8),
                          (0, 0, 140), -1)
            cv2.putText(cell, "NO SENSORS - tap the phone screen",
                        (12, tile_h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                        (255, 255, 255), 1, cv2.LINE_AA)
        canvas[top:top + tile_h, 8:8 + LEFT_W - 16] = cell
        cv2.rectangle(canvas, (8, top), (8 + LEFT_W - 16, top + tile_h), col, 2)
        cv2.rectangle(canvas, (8, top), (8 + LEFT_W - 16, top + 22), (0, 0, 0), -1)
        cv2.putText(canvas, dv["name"][:22], (14, top + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, col, 1, cv2.LINE_AA)

        # --- that phone's 3D reconstruction, same orbit as the building
        rec_h = rh - tile_h - 10
        if rec_h > 40:
            rtop = top + tile_h + 6
            rp = np.full((rec_h, LEFT_W - 16, 3), 14, np.uint8)
            pc = p3.get(dv["id"])
            if pc is not None and pc.get("cloud") is not None and len(pc["cloud"]):
                cl = pc["cloud"]
                cen = np.median(cl, axis=0)
                spread = np.percentile(np.abs(cl - cen), 85, axis=0)
                d = float(max(1.5, 2.6 * float(np.linalg.norm(spread))))
                u, v, z, cc = project(cl, pc["rgbc"], R,
                                      np.array([0, 0, d], np.float32),
                                      (LEFT_W - 16) / 1.5, cen, LEFT_W - 16, rec_h)
                o = np.argsort(-z)
                splat(rp, u[o], v[o], cc[o][:, ::-1],
                      int(np.clip(round(2.0 * 10.0 / d), 1, 3)), LEFT_W - 16, rec_h)
                m = pc["meta"]
                cv2.putText(rp, "%s  %s" % (m.get("room_id") or "unplaced",
                                            m.get("confidence", "")),
                            (8, rec_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                            (170, 170, 170), 1)
            else:
                cv2.putText(rp, "reconstructing...", (10, rec_h // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 80, 80), 1, cv2.LINE_AA)
            canvas[rtop:rtop + rec_h, 8:8 + LEFT_W - 16] = rp
            cv2.rectangle(canvas, (8, rtop), (8 + LEFT_W - 16, rtop + rec_h),
                          (55, 55, 60), 1)




def _tab_bar(canvas):
    """Draw the tab strip and register its click regions."""
    y = VIDEO_BOTTOM[0] + 4
    h = 26
    if y + h > H:
        return
    w = (LEFT_W - 16) // len(LEFT_TABS)
    for i, name in enumerate(LEFT_TABS):
        x = 8 + i * w
        on = (i == LEFT_TAB[0])
        cv2.rectangle(canvas, (x, y), (x + w - 3, y + h),
                      (48, 66, 88) if on else (22, 24, 28), -1)
        if on:
            cv2.line(canvas, (x, y + h - 2), (x + w - 3, y + h - 2),
                     (120, 200, 255), 2)
        cv2.putText(canvas, name, (x + 10, y + 18), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, (170, 220, 255) if on else (120, 120, 128), 1,
                    cv2.LINE_AA)
        _HIT["tab_%d" % i] = (x, y, x + w - 3, y + h)

def left_panel(canvas):
    """Tabbed lower-left panels. Never draws at x >= LEFT_W."""
    _HIT.clear()
    _tab_bar(canvas)
    if LEFT_TAB[0] != 0:            # PHOTOS owns the gallery + preset picker
        return
    x0, x1 = 8, LEFT_W - 10
    if x1 <= x0:
        return
    # Build upward from whatever the join QR left free, not from the window
    # edge: the badge sits at the bottom and the gallery used to run under it.
    y = (LEFT_BOTTOM[0] if LEFT_BOTTOM[0] else H) - 26

    # preset dropdown (collapsed row, or an open list above it)
    rowh = 22
    if PRESET_OPEN[0]:
        n = len(GEN_PRESETS)
        top = max(HEADER_H + 4, y - rowh * (n + 1) - 4)
        for i, pr in enumerate(GEN_PRESETS):
            ry = top + i * rowh
            if ry + rowh > y:
                break
            sel = (i == GEN_SEL[0] % len(GEN_PRESETS))
            cv2.rectangle(canvas, (x0, ry), (x1, ry + rowh - 2),
                          (40, 60, 80) if sel else (24, 26, 30), -1)
            cv2.putText(canvas, pr[:34], (x0 + 8, ry + 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                        (150, 230, 255) if sel else (190, 190, 195), 1, cv2.LINE_AA)
            _HIT["preset_%d" % i] = (x0, ry, x1, ry + rowh - 2)
        y = top - 6
    cur = GEN_PRESETS[GEN_SEL[0] % len(GEN_PRESETS)]
    cv2.rectangle(canvas, (x0, y - rowh), (x1, y), (30, 34, 40), -1)
    cv2.rectangle(canvas, (x0, y - rowh), (x1, y), (90, 120, 150), 1)
    cv2.putText(canvas, ("v  " if PRESET_OPEN[0] else ">  ") + cur[:30],
                (x0 + 8, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.44,
                (170, 220, 255), 1, cv2.LINE_AA)
    _HIT["preset_toggle"] = (x0, y - rowh, x1, y)
    y -= rowh + 8

    # filmstrip of captured photos, newest first
    caps = list(CAPTURES)
    if caps:
        tw, th = 74, 54
        per = max(1, (x1 - x0) // (tw + 6))
        rows = min(2, (len(caps) + per - 1) // per)
        strip_h = rows * (th + 6)
        sy = y - strip_h
        cv2.putText(canvas, "PHOTOS (%d)  [p] generate  [,]/[.] select"
                    % len(caps), (x0, sy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    (150, 190, 220), 1, cv2.LINE_AA)
        for i, (cf, dev, _mt) in enumerate(caps[:per * rows]):
            cx = x0 + (i % per) * (tw + 6)
            cy = sy + (i // per) * (th + 6)
            if cx + tw > x1 or cy + th > H:
                continue
            im = _thumb(cf, tw, th)
            if im is None:
                continue
            canvas[cy:cy + th, cx:cx + tw] = im
            on = (i == CAP_SEL[0] % max(len(caps), 1))
            cv2.rectangle(canvas, (cx, cy), (cx + tw, cy + th),
                          (120, 255, 190) if on else (70, 70, 78), 2 if on else 1)
            _HIT["cap_%d" % i] = (cx, cy, cx + tw, cy + th)


_THUMBS = {}


def _thumb(path, w, h):
    """Cached thumbnail; re-read only when the file changes."""
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    key = (path, w, h)
    got = _THUMBS.get(key)
    if got is not None and got[0] == mt:
        return got[1]
    im = cv2.imread(path)
    if im is None:
        return None
    im = cv2.resize(im, (w, h))
    _THUMBS[key] = (mt, im)
    return im


def panel_click(x, y):
    """Handle a click in the left column. True if it was ours."""
    for name, (a, b, c, d) in list(_HIT.items()):
        if a <= x <= c and b <= y <= d:
            if name == "preset_toggle":
                PRESET_OPEN[0] = not PRESET_OPEN[0]
            elif name.startswith("preset_"):
                GEN_SEL[0] = int(name.split("_")[1])
                PRESET_OPEN[0] = False
                print("[gen] preset -> %s"
                      % GEN_PRESETS[GEN_SEL[0] % len(GEN_PRESETS)], flush=True)
            elif name.startswith("tab_"):
                _t = int(name.split("_")[1])
                # clicking the open tab closes it, back to map + video only
                LEFT_TAB[0] = -1 if LEFT_TAB[0] == _t else _t
                print("[ui] tab -> %s" % LEFT_TABS[LEFT_TAB[0]], flush=True)
            elif name.startswith("cap_"):
                CAP_SEL[0] = int(name.split("_")[1])
                print("[gen] photo -> %s" % os.path.basename(
                    CAPTURES[CAP_SEL[0]][0]), flush=True)
            return True
    return False


REC_DIR = os.path.expanduser("~/plans/recordings")
REC_ON_START = os.environ.get("RECORD", "0") == "1"
REC_FPS = float(os.environ.get("RECORD_FPS", "12"))
LEFT_TABS = ("PHOTOS", "IMAGINED", "SENSORS")
LEFT_TAB = [-1]          # -1 = closed. Only the map and the live video
                         # share the screen unless a tab is opened.
LEFT_BOTTOM = [0]        # top edge of the join QR, or H when hidden
VIDEO_BOTTOM = [0]       # lowest y the live tile occupies; nothing else may
                         # draw above this in the left column
_REC_PENDING = [False]   # set from RECORD=1 at startup
_REC = {"w": None, "path": None, "frames": 0, "t0": 0.0}


def _rec_toggle(canvas):
    """Start or stop recording. Returns a short status for the operator."""
    if _REC["w"] is not None:
        _rec_stop()
        return "recording saved"
    try:
        os.makedirs(REC_DIR, exist_ok=True)
        n = 1
        while os.path.exists(os.path.join(REC_DIR, "session_%03d.avi" % n)):
            n += 1
        path = os.path.join(REC_DIR, "session_%03d.avi" % n)
        h, w = canvas.shape[:2]
        vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"),
                             REC_FPS, (w, h))
        if not vw.isOpened():
            print("[rec] could not open a writer for %dx%d" % (w, h), flush=True)
            return "recording failed"
        _REC.update(w=vw, path=path, frames=0, t0=time.time())
        print("[rec] recording -> %s" % path, flush=True)
        return "recording"
    except Exception as e:
        print("[rec] start failed: %s" % str(e)[:90], flush=True)
        return "recording failed"


def _rec_stop():
    if _REC["w"] is None:
        return
    try:
        _REC["w"].release()
    except Exception:
        pass
    dur = time.time() - _REC["t0"]
    _saved_path = _REC["path"]
    print("[rec] saved %s (%d frames, %.1fs)"
          % (_REC["path"], _REC["frames"], dur), flush=True)
    _REC.update(w=None, path=None, frames=0,
                last_saved=_saved_path, last_t=time.time())


def _rec_write(canvas):
    if _REC["w"] is None:
        return
    try:
        _REC["w"].write(canvas)
        _REC["frames"] += 1
    except Exception:
        pass



def _rec_badge(canvas):
    """Recording indicator + where the file is written. Left column only."""
    y = HEADER_H + 6
    if _REC["w"] is not None:
        secs = time.time() - _REC["t0"]
        cv2.rectangle(canvas, (8, y), (min(LEFT_W - 10, 8 + 300), y + 38),
                      (0, 0, 0), -1)
        cv2.circle(canvas, (24, y + 13), 6, (60, 60, 235), -1)
        cv2.putText(canvas, "REC  %d:%02d  [m] stop" % (secs // 60, secs % 60),
                    (38, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.46,
                    (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(canvas, "~/plans/recordings/%s"
                    % os.path.basename(_REC["path"] or ""),
                    (14, y + 33), cv2.FONT_HERSHEY_SIMPLEX, 0.36,
                    (170, 210, 240), 1, cv2.LINE_AA)
    elif _REC.get("last_saved") and time.time() - _REC.get("last_t", 0) < 12:
        cv2.rectangle(canvas, (8, y), (min(LEFT_W - 10, 8 + 300), y + 34),
                      (0, 0, 0), -1)
        cv2.putText(canvas, "saved  ~/plans/recordings/", (14, y + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.40, (120, 255, 190), 1,
                    cv2.LINE_AA)
        cv2.putText(canvas, os.path.basename(_REC["last_saved"]), (14, y + 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 230, 255), 1,
                    cv2.LINE_AA)
    else:
        cv2.putText(canvas, "[m] record session -> ~/plans/recordings/",
                    (10, H - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.34,
                    (120, 140, 160), 1, cv2.LINE_AA)

def _maybe_shot(canvas):
    """Save the displayed frame when SHOT_EVERY is set. Any screen, not just
    the dashboard: the setup screen is the one that gates everything else."""
    if _REC_PENDING[0] and _REC["w"] is None:
        _REC_PENDING[0] = False        # first rendered frame: canvas size known
        _rec_toggle(canvas)
    _rec_write(canvas)
    if SHOT_EVERY <= 0 or time.time() - _SHOT["t"] < SHOT_EVERY:
        return
    _SHOT["t"] = time.time()
    _SHOT["n"] += 1
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        cv2.imwrite(os.path.join(SHOT_DIR, "frame_%03d.png" % _SHOT["n"]), canvas)
    except Exception:
        pass


def render_dashboard(canvas, dets):
    """Everything in one view: phones left, building right, scenegraph overlaid."""
    bw, bh = W - LEFT_W, H - HEADER_H
    pane = np.full((bh, bw, 3), 14, np.uint8)
    R = rot(view["yaw"], view["pitch"])
    cam = np.array([0, 0, view["dist"]], np.float32)
    f = bw / 1.5
    rad = int(np.clip(round(1.6 * SPAN / max(view["dist"], 1e-3)), 1, 5))

    filled = 0
    shown = [lv for lv in LEVELS if view["levels"] in ("both", lv)]
    for lv in shown:
        P, C = LEVELS[lv]["acc"].get()
        if P is None:
            continue
        filled += len(P)
        u, v, z, cc = project(P, C, R, cam, f, BCEN, bw, bh)
        o = np.argsort(-z)
        splat(pane, u[o], v[o], cc[o][:, ::-1], rad, bw, bh)

    Q, Cc = [], []
    for lv in shown:
        if SLAB_SHOW[0]:
            Q.append(LEVELS[lv]["quads"]); Cc.append(LEVELS[lv]["qcols"])
        else:
            Q.append(LEVELS[lv]["walls_only"]); Cc.append(LEVELS[lv]["wcols_only"])
    oq, oc, nobj = object_primitives(R, cam, f, bw, bh)
    if oq is not None:
        Q.append(oq); Cc.append(oc)
    plan_render.draw_walls(pane, np.concatenate(Q), np.concatenate(Cc),
                           R, cam, f, BCEN, bw, bh, edge=True)

    # each phone's POV: camera position plus the cone it is actually looking down
    with _p3lock:
        p3 = {k: dict(v) for k, v in phone3d.items()}

    def to_px(p3d):
        q = (np.asarray(p3d, np.float32) - BCEN) @ R.T + cam
        if q[2] <= 0.25:
            return None
        return (int(f * q[0] / q[2] + bw / 2), int(bh / 2 - f * q[1] / q[2]))

    # where each phone has walked
    for pid, tr_ in TRAILS.items():
        pts_t = [to_px(np.array([x, y, LEVELS[l]["base_z"] + 0.08], np.float32))
                 for (x, y, l) in tr_ if l in LEVELS]
        pts_t = [q for q in pts_t if q]
        if len(pts_t) > 1:
            arr_t = np.array(pts_t, np.int32)
            cv2.polylines(pane, [arr_t], False, (0, 0, 0), 5, cv2.LINE_AA)
            cv2.polylines(pane, [arr_t], False, device_color(pid), 3, cv2.LINE_AA)
            for q_ in arr_t[::12]:               # breadcrumbs along the path
                cv2.circle(pane, tuple(int(z) for z in q_), 3,
                           device_color(pid), -1)

    for pid, pc in p3.items():
        m = pc["meta"]
        lv_m = m.get("level")
        if lv_m not in LEVELS:
            continue
        # Draw the phone even when it is placed on the other storey. Skipping it
        # meant the marker silently vanished and the map looked frozen, with no
        # indication that the pose had simply landed on a floor we were not
        # currently showing.
        off_floor = lv_m not in shown
        base_z = LEVELS[lv_m]["base_z"]
        cp = m.get("cam_pos")
        origin = (np.array([cp[0], cp[1], base_z + 1.5], np.float32) if cp
                  else (np.array([m["centroid"][0], m["centroid"][1],
                                  base_z + 1.5], np.float32)
                        if m.get("centroid") else None))
        if origin is None:
            continue
        col = device_color(pid)
        _mk = _MARKER_LOG.get(pid)
        if _mk is None or abs(_mk[0] - float(m.get("cam_pos", [0, 0, 0])[0])) > 0.25 \
                or abs(_mk[1] - float(m.get("cam_pos", [0, 0, 0])[1])) > 0.25:
            _MARKER_LOG[pid] = (float(m.get("cam_pos", [0, 0, 0])[0]),
                                float(m.get("cam_pos", [0, 0, 0])[1]))
            print("[marker] %s world=(%.2f, %.2f) level=%s%s"
                  % (pid, _MARKER_LOG[pid][0], _MARKER_LOG[pid][1], lv_m,
                     "  OFF-FLOOR(dimmed)" if off_floor else ""), flush=True)
        if off_floor:
            col = tuple(int(c * 0.55) for c in col)   # dimmed: on the other floor
        hd = m.get("heading_deg")
        if hd is not None:
            # A 4 m wedge at 22% opacity was rotating correctly but was too small
            # and too faint to read at building zoom — it looked frozen. Scale it
            # with the view and add a bold arrow, so a turn is unmistakable.
            half = np.radians(HFOV_DEG / 2.0)
            a = np.radians(hd)
            reach = float(np.clip(view["dist"] * 0.14, 3.0, 14.0))
            # compass bearings: x = sin(a), y = cos(a). Using cos for x drew
            # the wedge 90 degrees off the direction of travel.
            l = origin + np.array([reach * np.sin(a - half),
                                   reach * np.cos(a - half), 0], np.float32)
            r_ = origin + np.array([reach * np.sin(a + half),
                                    reach * np.cos(a + half), 0], np.float32)
            tip = origin + np.array([reach * 1.15 * np.sin(a),
                                     reach * 1.15 * np.cos(a), 0], np.float32)
            po, pl, pr, pt = to_px(origin), to_px(l), to_px(r_), to_px(tip)
            if po and pl and pr:
                tri = np.array([po, pl, pr], np.int32)
                ov = pane.copy()
                cv2.fillConvexPoly(ov, tri, col)
                cv2.addWeighted(ov, 0.42, pane, 0.58, 0, pane)
                cv2.polylines(pane, [tri], True, col, 2, cv2.LINE_AA)
            if po and pt:
                cv2.arrowedLine(pane, po, pt, (255, 255, 255), 5, cv2.LINE_AA,
                                tipLength=0.28)
                cv2.arrowedLine(pane, po, pt, col, 3, cv2.LINE_AA, tipLength=0.28)
        px = to_px(origin)
        if px:
            cv2.circle(pane, px, 7, col, -1)
            cv2.circle(pane, px, 10, (255, 255, 255), 1)
            lbl = pc["name"][:14]
            if hd is not None:
                lbl += "  %.0f deg" % (hd % 360)
            if off_floor:
                lbl += "  [%s]" % lv_m
            cv2.putText(pane, lbl, (px[0] + 13, px[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, col, 1, cv2.LINE_AA)



    # every sensor the phone reports, with a sparkline of how each is moving
    if SENSOR_SHOW[0]:
        with _plock:
            ids = [d["id"] for d in phones["list"]][:1]
        for did in ids:
            sv = SENSORS.get(did) or {}
            hist = SENSOR_HIST.get(did) or {}
            keys = [k for k in sv if isinstance(sv[k], (int, float))][:14]
            if not keys:
                continue
            # Left column, on `canvas`: on `pane` this covered the map.
            if LEFT_TAB[0] != 2:
                continue
            pw4, rh4 = LEFT_W - 24, 22
            x4 = 12
            hh4 = 30 + rh4 * len(keys)
            y4 = max(HEADER_H + 6, H - hh4 - 10)
            if y4 + hh4 > H or x4 + pw4 > LEFT_W:
                continue
            sub4 = canvas[y4:y4 + hh4, x4:x4 + pw4]
            cv2.addWeighted(sub4, 0.18, np.zeros_like(sub4), 0.82, 0, sub4)
            cv2.rectangle(canvas, (x4, y4), (x4 + pw4, y4 + hh4), (90, 90, 100), 1)
            cv2.putText(canvas, "SENSORS  %s  [g] hide" % did[:8], (x4 + 8, y4 + 17),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.40, (150, 210, 255), 1, cv2.LINE_AA)
            yy4 = y4 + 34
            for k4 in keys:
                cv2.putText(canvas, "%-9s %8.2f" % (k4[:9], float(sv[k4])),
                            (x4 + 8, yy4), cv2.FONT_HERSHEY_SIMPLEX, 0.36,
                            (215, 215, 215), 1, cv2.LINE_AA)
                ser = hist.get(k4) or []
                if len(ser) > 3:
                    a4 = np.array(ser[-90:], np.float32)
                    lo4, hi4 = float(a4.min()), float(a4.max())
                    rng4 = max(hi4 - lo4, 1e-6)
                    gx0, gw4, gh4 = x4 + 170, 140, 14
                    xs4 = np.linspace(gx0, gx0 + gw4, len(a4)).astype(np.int32)
                    ys4 = (yy4 - 4 - (a4 - lo4) / rng4 * gh4).astype(np.int32)
                    cv2.polylines(canvas, [np.stack([xs4, ys4], -1)], False,
                                  (120, 220, 160), 1, cv2.LINE_AA)
                yy4 += rh4


    # the clicked join anchor
    if ANCHOR["x"] is not None and ANCHOR["level"] in shown:
        ap = to_px(np.array([ANCHOR["x"], ANCHOR["y"],
                             LEVELS[ANCHOR["level"]]["base_z"] + 0.05], np.float32))
        if ap:
            cv2.drawMarker(pane, ap, (80, 255, 180), cv2.MARKER_CROSS, 22, 2)
            cv2.circle(pane, ap, 13, (80, 255, 180), 1)
            cv2.putText(pane, "QR anchor", (ap[0] + 15, ap[1] + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (80, 255, 180), 1, cv2.LINE_AA)

    # Scenegraph tree. Drawn in the LEFT column, never over the map, and only
    # when asked for: it is reference detail, not something worth hiding the
    # building behind.
    with _sglock:
        lines, cnt = SG.tree_lines(max_rooms=8), SG.counts()
    if not SHOW_TREE[0]:
        lines = []
    pw = LEFT_W - 28
    phh = min(bh - 30, 40 + 20 * (len(lines) + 1))
    ox, oy = 14, 14
    if SHOW_TREE[0]:
        sub = pane[oy:oy + phh, ox:ox + pw]
        cv2.addWeighted(sub, 0.20, np.zeros_like(sub), 0.80, 0, sub)
    if SHOW_TREE[0]:
        cv2.rectangle(pane, (ox, oy), (ox + pw, oy + phh), (85, 85, 92), 1)
    cv2.putText(pane, "" if not SHOW_TREE[0] else
                "SCENEGRAPH  %d objects  %d rooms  %d people"
                % (cnt["confirmed"], cnt["rooms_with_objects"], cnt["people"]),
                (ox + 10, oy + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (150, 210, 255), 1, cv2.LINE_AA)
    yy = oy + 42
    for ln in lines:
        if yy > oy + phh - 6:
            break
        c = (150, 210, 255) if ln.startswith("|  +-") else (
            (255, 205, 140) if ln.startswith("+-") else (185, 185, 185))
        cv2.putText(pane, ln[:52], (ox + 10, yy), cv2.FONT_HERSHEY_SIMPLEX, 0.38, c, 1, cv2.LINE_AA)
        yy += 20

    canvas[HEADER_H:, LEFT_W:] = pane
    panel_phones(canvas, dets)

    # Object tally lives in the LEFT column, never over the map: the walked
    # trail and the building are the point of that view, and a panel sitting on
    # top of them hides exactly what the operator is trying to read.
    with _plock:
        _any_live = bool(phones["list"])
    with _sglock:
        live_counts = {}
        if _any_live or SESSION["started"]:
            for tr in SG.tracks:
                if (tr.confirmed and tr.cls not in scenegraph3d.TRANSIENT
                        and tr.last >= SESSION["t0"]):
                    live_counts[tr.cls] = live_counts.get(tr.cls, 0) + 1
    if live_counts:
        items = sorted(live_counts.items(), key=lambda kv: -kv[1])[:8]
        hh3 = 26 + 17 * len(items)
        x3, y3 = 8, H - hh3 - 8
        sub3 = canvas[y3:y3 + hh3, x3:x3 + LEFT_W - 16]
        cv2.addWeighted(sub3, 0.16, np.zeros_like(sub3), 0.84, 0, sub3)
        cv2.rectangle(canvas, (x3, y3), (x3 + LEFT_W - 16, y3 + hh3),
                      (85, 85, 92), 1)
        cv2.putText(canvas, "objects counted (%d)" % sum(live_counts.values()),
                    (x3 + 8, y3 + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    (150, 210, 255), 1, cv2.LINE_AA)
        mx3 = max(v for _k, v in items)
        yy3 = y3 + 31
        for k3, v3 in items:
            w3 = int((LEFT_W - 150) * v3 / max(mx3, 1))
            c3 = OBJ_COL.get(k3, DEF_COL)
            cv2.rectangle(canvas, (x3 + 92, yy3 - 8), (x3 + 92 + max(w3, 2), yy3 - 1),
                          c3, -1)
            cv2.putText(canvas, k3[:12], (x3 + 8, yy3), cv2.FONT_HERSHEY_SIMPLEX,
                        0.34, (215, 215, 215), 1, cv2.LINE_AA)
            cv2.putText(canvas, str(v3), (x3 + 96 + max(w3, 2), yy3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.34, c3, 1, cv2.LINE_AA)
            yy3 += 17

    # newest generated image, shown large so the operator can actually see it
    if time.time() - _GEN_DRAW.get("beat", 0) > 5.0:
        _GEN_DRAW["beat"] = time.time()
        print("[gen] state: GEN_IMG=%d keys=%s canvas=%dx%d LEFT_W=%d HEADER_H=%d"
              % (len(GEN_IMG), list(GEN_IMG)[:3], W, H, LEFT_W, HEADER_H),
              flush=True)
    if GEN_IMG and LEFT_TAB[0] == 1:
        did_g = max(GEN_IMG, key=lambda k: GEN_IMG[k][2])
        gim, gpre, gmt = GEN_IMG[did_g]
        gh = int(min(bh * 0.62, 460))
        gw = int(gh * gim.shape[1] / max(gim.shape[0], 1))
        # Draw into `canvas`, NOT `pane`: pane was already flushed to canvas
        # above, so anything written to it now is invisible.
        # Left column, bottom: visible without covering any of the map.
        gw2 = LEFT_W - 28
        avail = 132        # small: the video feed and counts must stay readable
        gh = int(gw2 * gim.shape[0] / max(gim.shape[1], 1))
        if gh > avail:                              # scale to the free band
            gh = avail
            gw2 = int(gh * gim.shape[1] / max(gim.shape[0], 1))
        gw = min(gw2, LEFT_W - 28)
        gx = LEFT_W - gw - 12          # tucked to the column edge
        gy = max(HEADER_H + 8, H - 200 - gh)
        if gy + gh < H and gx > 0:
            canvas[gy:gy + gh, gx:gx + gw] = cv2.resize(gim, (gw, gh))
            cv2.rectangle(canvas, (gx, gy), (gx + gw, gy + gh), (120, 255, 190), 2)
            cv2.rectangle(canvas, (gx, gy), (gx + gw, gy + 26), (0, 0, 0), -1)
            cv2.putText(canvas, "IMAGINED: %s" % gpre, (gx + 8, gy + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (120, 255, 190), 1, cv2.LINE_AA)
            cv2.putText(canvas, "%.0fs ago" % (time.time() - gmt),
                        (gx + gw - 70, gy + gh - 8), cv2.FONT_HERSHEY_SIMPLEX,
                        0.38, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.putText(canvas, "[w] save  [d] dismiss  [p] regenerate  [n] preset",
                        (gx + 6, min(H - 6, gy + gh + 16)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.36, (170, 210, 240), 1,
                        cv2.LINE_AA)
            if _GEN_DRAW.get("shown") != gmt:
                _GEN_DRAW["shown"] = gmt
                print("[gen] VISIBLE on canvas: %s %dx%d at (%d,%d) of %dx%d"
                      % (did_g, gw, gh, gx, gy, W, H), flush=True)
        elif _GEN_DRAW.get("warn") != (gw, gh):
            _GEN_DRAW["warn"] = (gw, gh)
            print("[gen] SKIPPED: %dx%d at (%d,%d) will not fit %dx%d"
                  % (gw, gh, gx, gy, W, H), flush=True)

    # end-of-capture summary: what was actually counted, as a chart rather than
    # a number buried in a tree
    if SUMMARY["active"]:
        # narrow column docked left of the building pane; never covers the map
        # centre or the generated image on the right
        cw2 = max(240, min(int(W * 0.23), 380))
        ch2 = H - HEADER_H - 24
        # Left column only. LEFT_W + 14 is inside the building pane and covered
        # the very thing the summary is meant to be read alongside.
        ox2, oy2 = 8, HEADER_H + 12
        cw2 = min(cw2, LEFT_W - 16)
        if ox2 + cw2 > LEFT_W:
            cw2 = max(120, LEFT_W - ox2 - 8)
        sub2 = canvas[oy2:oy2 + ch2, ox2:ox2 + cw2]
        cv2.addWeighted(sub2, 0.12, np.zeros_like(sub2), 0.88, 0, sub2)
        cv2.rectangle(canvas, (ox2, oy2), (ox2 + cw2, oy2 + ch2), (140, 200, 240), 2)
        cv2.putText(canvas, "CAPTURE COMPLETE  -  %s" % SUMMARY["name"],
                    (ox2 + 20, oy2 + 34), cv2.FONT_HERSHEY_SIMPLEX, 0.68,
                    (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, "%s points saved   %d object types   [any key] dismiss"
                    % ("{:,}".format(SUMMARY["points"]), len(SUMMARY["counts"])),
                    (ox2 + 20, oy2 + 58), cv2.FONT_HERSHEY_SIMPLEX, 0.46,
                    (170, 210, 240), 1, cv2.LINE_AA)
        items = sorted(SUMMARY["counts"].items(), key=lambda kv: -kv[1])[:18]
        if not items:
            cv2.putText(canvas, "no objects confirmed in this capture",
                        (ox2 + 24, oy2 + 100), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (150, 150, 150), 1, cv2.LINE_AA)
        else:
            mx = max(v for _k, v in items)
            bw2 = cw2 - 220
            y2 = oy2 + 92
            rowh = min(30, (ch2 - 120) // max(len(items), 1))
            for k, v in items:
                w2 = int(bw2 * v / max(mx, 1))
                col2 = OBJ_COL.get(k, DEF_COL)
                cv2.rectangle(canvas, (ox2 + 150, y2 - rowh + 8),
                              (ox2 + 150 + max(w2, 2), y2 + 2), col2, -1)
                cv2.putText(canvas, k[:18], (ox2 + 20, y2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.44, (225, 225, 225), 1,
                            cv2.LINE_AA)
                cv2.putText(canvas, str(v), (ox2 + 158 + max(w2, 2), y2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.44, col2, 1, cv2.LINE_AA)
                y2 += rowh

    # header
    cv2.rectangle(canvas, (0, 0), (W, HEADER_H), (26, 26, 30), -1)
    with _plock:
        ndev = len(phones["list"])
    cv2.putText(canvas, "RECAST  -  1700 Westlake Ave N", (14, 27),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(canvas, "phones %d  objects %d  filled %s pts  floors[f] %s  view[v] %s  scale x%.3f %s"
                % (ndev, nobj, "{:,}".format(filled), view["levels"],
                   VIEW_MODE[0], CAL["scale_k"],
                   "LOCKED" if CAL["locked"] else
                   ("calibrating %d/%d" % (CAL["samples"], CAL_MIN_SAMPLES)
                    if CAL["samples"] else "(uncal)")),
                (LEFT_W + 14, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (170, 200, 230), 1)
    return nobj


def tab_join(canvas, dets):
    ch = H - TAB_H
    pane = np.full((ch, W, 3), 16, np.uint8)
    with _plock:
        devs, url = list(phones["list"]), phones["url"]
    qr = cv2.imread(QR_PATH) if os.path.exists(QR_PATH) else None
    qs = min(360, ch - 220)
    if qr is not None:
        pane[110:110 + qs, 60:60 + qs] = cv2.resize(qr, (qs, qs),
                                                    interpolation=cv2.INTER_NEAREST)
    else:
        cv2.rectangle(pane, (60, 110), (60 + qs, 110 + qs), (60, 60, 60), 2)
    cv2.putText(pane, "Add a phone camera", (60, 60), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(pane, url or BRIDGE, (60, 140 + qs), cv2.FONT_HERSHEY_SIMPLEX,
                0.78, (90, 230, 255), 2, cv2.LINE_AA)
    cv2.putText(pane, "no install - walk the building to fill in the 3D map",
                (60, 172 + qs), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (130, 130, 130), 1, cv2.LINE_AA)
    x0 = 60 + qs + 70
    cv2.putText(pane, "Connected  (%d)" % len(devs), (x0, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    with _p3lock:
        p3 = {k: dict(v) for k, v in phone3d.items()}
    for i, dv in enumerate(devs[:6]):
        y = 105 + i * 34
        cv2.circle(pane, (x0 + 10, y - 5), 7, device_color(dv["id"]), -1)
        m = p3.get(dv["id"], {}).get("meta", {})
        cv2.putText(pane, "%-14s %s" % (dv["name"][:14],
                                        m.get("room_id") or "locating..."),
                    (x0 + 28, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
    cv2.putText(pane, "depth: %s   device: %s" % (DEPTH_NAME, DEV),
                (x0, ch - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (110, 110, 110), 1, cv2.LINE_AA)
    canvas[TAB_H:] = pane


QR_IMG, QR_SZ = None, 150


def draw_qr_badge(canvas):
    """Always-visible join QR — a phone should join without navigating tabs first."""
    global QR_IMG
    if QR_IMG is None and os.path.exists(QR_PATH):
        q = cv2.imread(QR_PATH)
        if q is not None:
            QR_IMG = cv2.resize(q, (QR_SZ, QR_SZ), interpolation=cv2.INTER_NEAREST)
    with _plock:
        url, dl = phones["url"] or BRIDGE, list(phones["list"])
    # Not while a phone is already on, unless explicitly asked for with [b].
    try:
        with _plock:
            _joined = len(phones["list"])
    except Exception:
        _joined = 0
    if _joined and not SHOW_QR[0]:
        LEFT_BOTTOM[0] = H
        return
    pad, bw, bh = 14, min(QR_SZ + 150, LEFT_W - 28), QR_SZ + 62
    # left column, and strictly below the live video: the badge used to cover
    # the stream, which is the one thing the operator is watching
    x0, y0 = 8, max(VIDEO_BOTTOM[0] + 8, H - bh - pad)
    if y0 + bh > H:
        return
    LEFT_BOTTOM[0] = y0
    sub = canvas[y0:y0 + bh, x0:x0 + bw]
    cv2.addWeighted(sub, 0.15, np.zeros_like(sub), 0.85, 0, sub)
    cv2.rectangle(canvas, (x0, y0), (x0 + bw, y0 + bh), (90, 90, 95), 1)
    if QR_IMG is not None:
        canvas[y0 + 12:y0 + 12 + QR_SZ, x0 + 12:x0 + 12 + QR_SZ] = QR_IMG
    cv2.putText(canvas, "scan to add a phone   white %.1f in / pattern %.1f cm"
                % (QR_WHITE_M / 0.0254, QR_SIZE_M * 100),
                (x0 + 12, y0 + QR_SZ + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1, cv2.LINE_AA)
    cv2.putText(canvas, url.replace("https://", ""), (x0 + 12, y0 + QR_SZ + 48),
                cv2.FONT_HERSHEY_SIMPLEX, 0.40, (90, 230, 255), 1, cv2.LINE_AA)
    if CAL["samples"]:
        cv2.putText(canvas, "scale x%.3f %s (%d reads, hFOV~%.0f deg)"
                    % (CAL["scale_k"], "LOCKED" if CAL["locked"] else "...",
                       CAL["samples"], CAL["hfov_implied"] or 0),
                    (x0 + 12, y0 + QR_SZ + 62), cv2.FONT_HERSHEY_SIMPLEX, 0.40,
                    (120, 255, 190), 1, cv2.LINE_AA)
    else:
        cv2.putText(canvas, "scale uncalibrated - show the QR to a phone",
                    (x0 + 12, y0 + QR_SZ + 62), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    (150, 150, 150), 1, cv2.LINE_AA)
    for i, d in enumerate(dl[:3]):
        cy = y0 + QR_SZ + 30 + i * 15
        cv2.circle(canvas, (x0 + bw - 96, cy - 4), 5, device_color(d["id"]), -1)
        cv2.putText(canvas, d["name"][:12], (x0 + bw - 86, cy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, device_color(d["id"]), 1, cv2.LINE_AA)


# ---------------- start workers only once everything above is defined --------
threading.Thread(target=poll_phones, daemon=True).start()
threading.Thread(target=recon_worker, daemon=True).start()
threading.Thread(target=poll_generated, daemon=True).start()
threading.Thread(target=pdr_worker, daemon=True).start()

WIN = "Recast - Spark"
cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
cv2.resizeWindow(WIN, W, H)
try:
    cv2.moveWindow(WIN, 0, 0)
except Exception:
    pass
_sized = False


def _match_window_size():
    """Render at the window's true client size so nothing is resampled.

    The canvas was built at full screen resolution while the window manager gave
    the window a slightly smaller client area, so cv2.imshow scaled every frame
    down — resampling all text and geometry, which is what looked pixelated.
    Rendering 1:1 removes the scaling entirely.
    """
    global W, H, LEFT_W
    try:
        x, y, ww, hh = cv2.getWindowImageRect(WIN)
    except Exception:
        return False
    if ww < 320 or hh < 240:
        return False
    if abs(ww - W) < 2 and abs(hh - H) < 2:
        return False
    W, H = int(ww), int(hh)
    LEFT_W = max(280, int(W * 0.50))
    print("[ui] rendering 1:1 at %dx%d" % (W, H), flush=True)
    return True
cv2.setMouseCallback(WIN, on_mouse)
_REC_PENDING[0] = REC_ON_START   # started on the first rendered frame
fps, t0 = 0.0, time.time()
print("[setup] pick a floor and click where the QR is posted, then press enter",
      flush=True)

_i = 0
_last = {}
_last_size_check = 0.0
while not stop:
    canvas = np.full((H, W, 3), 18, np.uint8)
    _i += 1
    # people boxes only matter on the live tab; 3D tabs are projection-bound
    if _i % 3 == 0 or not _last:
        with _plock:
            devs, frames = list(phones["list"]), dict(phones["frames"])
        d = {}
        for dv in devs[:MAX_PHONE_3D]:
            img = frames.get(dv["id"])
            if img is not None:
                # no class filter: restricting to PERSON is why only people were
                # ever boxed, even though the scenegraph tracks 30+ classes
                d[dv["id"]] = seg_model.predict(img, imgsz=IMGSZ, conf=0.30,
                                                device=DEV, verbose=False)[0]
        _last = d
    dets = _last

    if SETUP["active"]:
        render_setup(canvas)
        _maybe_shot(canvas)
        cv2.imshow(WIN, canvas)
        if not _sized:
            cv2.resizeWindow(WIN, W, H); _sized = True
        k = cv2.waitKey(30) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord("1") and "level1" in LEVELS:
            SETUP.update(level="level1", x=None, y=None)
        elif k == ord("2") and "level2" in LEVELS:
            SETUP.update(level="level2", x=None, y=None)
        elif k == ord("r"):
            SETUP.update(x=None, y=None, heading=None)
            print("[setup] position cleared - click the plan to place the QR",
                  flush=True)
        elif k in (ord(","), ord("<"), 81) and SETUP["x"] is not None:
            SETUP["heading"] = ((SETUP["heading"] or 0.0) - 5.0) % 360.0
        elif k in (ord("."), ord(">"), 83) and SETUP["x"] is not None:
            SETUP["heading"] = ((SETUP["heading"] or 0.0) + 5.0) % 360.0
        elif k in (13, 10) and SETUP["x"] is not None:
            ANCHOR.update(x=SETUP["x"], y=SETUP["y"], level=SETUP["level"],
                          heading_deg=SETUP["heading"], set_at=time.time())
            view["levels"] = SETUP["level"]
            SETUP["active"] = False
            print("[setup] anchored at (%.2f, %.2f) on %s - starting"
                  % (ANCHOR["x"], ANCHOR["y"], ANCHOR["level"]), flush=True)
            try:
                import anchors as _anch
                d = _anch.load()
                d["anchors"] = [a for a in d["anchors"] if a.get("id") != "start"]
                d["anchors"].append(dict(
                    id="start", label="QR start (operator)", room_id="",
                    level=ANCHOR["level"], x=round(ANCHOR["x"], 2),
                    y=round(ANCHOR["y"], 2),
                    z=round(LEVELS[ANCHOR["level"]]["base_z"], 2),
                    heading_deg=round(_anch.facing_nearest_wall(
                        ANCHOR["x"], ANCHOR["y"], LEVELS[ANCHOR["level"]]["walls"]), 1),
                    url="", placement="clicked on the Spark before the run"))
                _anch.save(d)
            except Exception as e:
                print("[setup] anchor not persisted: %s" % str(e)[:70], flush=True)
        continue

    render_dashboard(canvas, dets)
    left_panel(canvas)
    _rec_badge(canvas)


    dt = time.time() - t0; t0 = time.time()
    fps = 0.9 * fps + 0.1 * (1 / dt if dt > 0 else 0)
    cv2.putText(canvas, "%.1f fps" % fps, (W - 96, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, (140, 140, 140), 1, cv2.LINE_AA)
    draw_qr_badge(canvas)

    _maybe_shot(canvas)
    cv2.imshow(WIN, canvas)
    k = cv2.waitKey(1) & 0xFF
    if SUMMARY["active"] and k != 255:
        SUMMARY["active"] = False
        if k in (ord("q"), 27):
            break
        k = 255                     # consume the dismissing keypress
    if k in (ord("q"), 27):
        break
    elif k == ord("r"):
        view.update(yaw=25.0, pitch=55.0, dist=SPAN * 1.05)
    elif k in (ord("["), ord("]")):
        # A scan placed with the wrong facing is otherwise unusable; rotating it
        # about the anchor is the operator's correction for a heading the
        # geometry got wrong.
        deg = -5.0 if k == ord("[") else 5.0
        ax = ANCHOR["x"] if ANCHOR["x"] is not None else float(BCEN[0])
        ay = ANCHOR["y"] if ANCHOR["y"] is not None else float(BCEN[1])
        a = np.radians(deg)
        Rz = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]], np.float32)
        moved = 0
        for _lv, _L in LEVELS.items():
            P, C = _L["acc"].get()
            if P is None or not len(P):
                continue
            Q = P.copy()
            Q[:, :2] = (P[:, :2] - [ax, ay]) @ Rz.T + [ax, ay]
            _L["acc"] = phone_slam.Accumulator(voxel=0.06)
            _L["acc"].add(Q, C)
            moved += len(Q)
        with _sglock:
            for tr in SG.tracks:
                tr.pos[:2] = (tr.pos[:2] - np.array([ax, ay], np.float32)) @ Rz.T \
                    + np.array([ax, ay], np.float32)
                if tr.yaw is not None:
                    tr.yaw = (tr.yaw + deg) % 180.0
        print("[scan] rotated %+.0f deg about (%.1f, %.1f): %s points"
              % (deg, ax, ay, "{:,}".format(moved)), flush=True)
    elif k == ord("k"):
        CAL.update(scale_k=1.0, samples=0, hfov_implied=None, spread=None,
                   locked=False, shown_m=None)
        del _cal_hits[:]
        print("[qr] calibration reset - show the QR again", flush=True)
    elif k == ord(","):
        if CAPTURES:
            CAP_SEL[0] = (CAP_SEL[0] - 1) % len(CAPTURES)
    elif k == ord("."):
        if CAPTURES:
            CAP_SEL[0] = (CAP_SEL[0] + 1) % len(CAPTURES)
    elif k == ord("p"):
        # Prefer the photo selected in the gallery so the same capture can be
        # regenerated under any preset; fall back to a live phone.
        dl_ = []
        if CAPTURES:
            dl_ = [CAPTURES[CAP_SEL[0] % len(CAPTURES)][1]]
        if not dl_:
            with _plock:
                dl_ = [d["id"] for d in phones["list"]]
        if dl_:
            pre = GEN_PRESETS[GEN_SEL[0] % len(GEN_PRESETS)]
            print("[gen] regenerating %s as '%s'" % (dl_[0], pre), flush=True)
            threading.Thread(target=request_generation, args=(dl_[0], pre),
                             daemon=True).start()
        else:
            print("[gen] no phone connected", flush=True)
    elif k == ord("n"):
        GEN_SEL[0] = (GEN_SEL[0] + 1) % len(GEN_PRESETS)
        print("[gen] preset: %s" % GEN_PRESETS[GEN_SEL[0]], flush=True)
    elif k == ord("t"):
        TRACE_ON[0] = not TRACE_ON[0]
        print("[trace] %s -> %s" % ("on" if TRACE_ON[0] else "off", TRACE_PATH),
              flush=True)
    elif k == ord("o"):
        SLAB_SHOW[0] = not SLAB_SHOW[0]
        print("[view] floor slab %s" % ("on" if SLAB_SHOW[0] else "off"), flush=True)
    elif k == ord("g"):
        SENSOR_SHOW[0] = not SENSOR_SHOW[0]
    elif k == ord("v"):
        VIEW_MODE[0] = {"video": "depth", "depth": "points",
                        "points": "video"}[VIEW_MODE[0]]
        print("[view] %s" % VIEW_MODE[0], flush=True)
    elif k == ord("e"):
        try:
            made = scan_session.export_all(LEVELS)
            if not made:
                print("[export] nothing accumulated yet", flush=True)
            for m in made:
                print("[export] %s %s -> %s (%s)"
                      % (m["level"], m["kind"], m["path"],
                         "%d pts" % m["points"] if m["kind"] == "ply"
                         else "%d verts / %d faces" % (m["vertices"], m["faces"])),
                      flush=True)
        except Exception as e:
            print("[export] failed: %s" % str(e)[:90], flush=True)
    elif k == ord("s"):
        try:
            with _sglock:
                _sg = SG.to_dict()
            _root, _meta = scan_session.save(
                time.strftime("scan_%Y%m%d_%H%M%S"), LEVELS, scenegraph=_sg,
                anchor=dict(ANCHOR), note="manual save")
            print("[scan] saved %s: %d points, %d objects"
                  % (_meta["name"], _meta["total_points"], _meta["objects"]), flush=True)
        except Exception as e:
            print("[scan] save failed: %s" % str(e)[:80], flush=True)
    elif k == ord("f"):
        view["levels"] = {"both": "level1", "level1": "level2",
                          "level2": "both"}[view["levels"]]
    elif k == ord("a"):
        # back to QR placement, keeping the current anchor as the starting point
        SETUP.update(active=True,
                     level=ANCHOR.get("level") or SETUP.get("level") or "level1",
                     x=ANCHOR.get("x", SETUP.get("x")),
                     y=ANCHOR.get("y", SETUP.get("y")),
                     heading=ANCHOR.get("heading_deg", SETUP.get("heading")))
        print("[setup] re-anchoring: click the plan to move the QR, "
              "[,]/[.] turn it, [r] clear, enter to confirm", flush=True)
    elif k == ord("m"):
        _rec_toggle(canvas)
    elif k == ord("b"):
        SHOW_QR[0] = not SHOW_QR[0]
        print("[ui] join QR %s" % ("shown" if SHOW_QR[0] else "auto"), flush=True)
    elif k == ord("x"):
        LEFT_TAB[0] = -1 if LEFT_TAB[0] >= len(LEFT_TABS) - 1 else LEFT_TAB[0] + 1
        print("[ui] tab -> %s"
              % ("closed" if LEFT_TAB[0] < 0 else LEFT_TABS[LEFT_TAB[0]]),
              flush=True)
    elif k == ord("j"):
        SHOW_TREE[0] = not SHOW_TREE[0]
        print("[ui] scenegraph tree %s" % ("shown" if SHOW_TREE[0] else "hidden"),
              flush=True)
    elif k == ord("w"):
        # Keep a copy. Regeneration overwrites snap_gen_<dev>.png, so without
        # this an image the user liked is lost the moment the next one runs.
        if GEN_IMG:
            did_s = max(GEN_IMG, key=lambda q: GEN_IMG[q][2])
            im_s, pre_s, _ = GEN_IMG[did_s]
            out_s = os.path.expanduser(
                "~/plans/saved/%s_%s_%d.png"
                % (did_s, str(pre_s).replace(" ", "_"), int(time.time())))
            os.makedirs(os.path.dirname(out_s), exist_ok=True)
            cv2.imwrite(out_s, im_s)
            print("[gen] saved %s" % out_s, flush=True)
        else:
            print("[gen] nothing to save yet", flush=True)
    elif k == ord("d"):
        # Clear the panel only; the file on disk is left alone.
        if GEN_IMG:
            GEN_IMG.clear()
            _GEN_DRAW.pop("shown", None)
            print("[gen] dismissed (file kept on disk)", flush=True)
    elif k == ord("c"):
        for L in LEVELS.values():
            L["acc"] = phone_slam.Accumulator(voxel=0.06)
        print("[acc] cleared", flush=True)

stop = True
_rec_stop()                      # never leave a half-written recording
cv2.destroyAllWindows()
