"""End-to-end test of the phone reconstruction chain, without needing a phone.

Feeds a real indoor frame of this building through the exact path the app runs:

  frame -> YOLO26-depth -> backproject -> phone_slam.place -> Accumulator

and reports what each stage produced. A phone frame differs mainly in FOV and
pose, so a pass here does not prove field accuracy — it proves the chain is
wired correctly and returns plausible geometry, which is the part worth knowing
before anyone walks the building.

  python test_recon_chain.py [--img ~/arlo-frames/lobby.jpg] [--level level2]
"""
import argparse, json, os, sys
import numpy as np
import cv2

sys.path.insert(0, os.path.expanduser("~/arlo-vision"))
import phone_slam

PLANS = os.path.expanduser("~/plans")
F2F = 15 * 0.3048 + 4 * 0.0254 + 0.60
DEPTH_STRIDE = 4


def backproject(img, d):
    Hh, Ww = img.shape[:2]
    if d.shape != (Hh, Ww):
        d = cv2.resize(d, (Ww, Hh), interpolation=cv2.INTER_LINEAR)
    st = DEPTH_STRIDE
    ds = d[::st, ::st]
    cs = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)[::st, ::st]
    h, w = ds.shape
    fx = w / (2.0 * np.tan(np.radians(68.0) / 2.0))
    uu, vv = np.meshgrid(np.arange(w), np.arange(h))
    P = np.stack([(uu - w / 2.0) * ds / fx,
                  -(vv - h / 2.0) * ds / fx, -ds], -1).reshape(-1, 3).astype(np.float32)
    C = cs.reshape(-1, 3).astype(np.uint8)
    ok = np.isfinite(P).all(1) & (ds.reshape(-1) > 0.15) & (ds.reshape(-1) < 25.0)
    return P[ok], C[ok]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=os.path.expanduser("~/arlo-frames/lobby.jpg"))
    ap.add_argument("--level", default="level2")
    a = ap.parse_args()

    print("=== stage 1: frame ===")
    img = cv2.imread(a.img)
    if img is None:
        print("FAIL: cannot read %s" % a.img); return 1
    print("  %s  %s" % (os.path.basename(a.img), img.shape))

    print("=== stage 2: YOLO26-depth ===")
    from ultralytics import YOLO
    m = YOLO("yolo26s-depth.pt")
    r = m.predict(img, verbose=False)[0]
    d = None
    for attr in ("depth", "depths"):
        o = getattr(r, attr, None)
        if o is not None:
            d = np.squeeze(o.data.cpu().numpy() if hasattr(o, "data")
                           else np.asarray(o)).astype(np.float32)
            break
    if d is None or d.ndim != 2:
        print("FAIL: no usable depth map"); return 1
    f = d[np.isfinite(d) & (d > 0)]
    print("  depth %s  min %.2f  med %.2f  max %.2f m" %
          (d.shape, f.min(), np.median(f), f.max()))

    print("=== stage 3: backproject ===")
    P, C = backproject(img, d)
    print("  %d points  extent x %.1f  y %.1f  z %.1f m" %
          (len(P), np.ptp(P[:, 0]), np.ptp(P[:, 1]), np.ptp(P[:, 2])))
    if len(P) < 400:
        print("FAIL: too few points"); return 1

    print("=== stage 4: plan anchoring ===")
    walls = np.load("%s/%s_walls_m.npy" % (PLANS, a.level))
    rooms = json.load(open("%s/%s_rooms.json" % (PLANS, a.level)))
    print("  plan: %d wall segs, %d rooms, manhattan axis %.1f deg" %
          (len(walls), len(rooms), phone_slam.plan_axis(walls)))
    # no phone sensors on a cached frame: gravity=None exercises the degraded path
    res = phone_slam.place(P, C, 0.0 if a.level == "level1" else F2F,
                           walls, rooms, gravity=None, compass_deg=None)
    if res is None:
        print("FAIL: place() returned None"); return 1
    print("  room      : %s" % res["room_id"])
    print("  confidence: %s" % res["confidence"])
    print("  yaw       : %.1f deg" % res["yaw_deg"])
    print("  footprint : %.1f m2   (room %.1f m2)" %
          (res["footprint_area"], res["room_area"]))
    print("  centroid  : %.1f, %.1f m" % tuple(res["centroid"]))
    z = res["points"][:, 2]
    print("  z range   : %.2f .. %.2f m (base %.2f)" %
          (z.min(), z.max(), 0.0 if a.level == "level1" else F2F))

    print("=== stage 5: accumulate ===")
    acc = phone_slam.Accumulator(voxel=0.06)
    acc.add(res["points"], res["colors"])
    n1 = len(acc)
    acc.add(res["points"], res["colors"])            # same frame must not double
    n2 = len(acc)
    Pa, Ca = acc.get()
    print("  voxels after 1 frame: %d" % n1)
    print("  after re-adding same frame: %d (dedup %s)" %
          (n2, "OK" if n2 == n1 else "FAILED"))
    print("  readback: %d pts, %d colors" % (len(Pa), len(Ca)))

    ok = (n2 == n1 and res["confidence"] != "unplaced" and len(Pa) > 0)
    print("\n%s" % ("CHAIN OK" if ok else "CHAIN RAN, review flags above"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
