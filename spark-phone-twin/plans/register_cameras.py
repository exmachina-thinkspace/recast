"""Localise each camera in the building by matching its floor footprint to a room.

The camera clouds are metric-ish but free-floating; the plan gives real room
polygons. Matching footprint shape + area to a room polygon recovers which room
a camera is in and roughly where, without needing intrinsics.

Honest limits: monocular depth scale drifts per camera (measured 5x spread), so
area matching is scored on *ratio* with generous tolerance and the result is a
ranked hypothesis, not a survey. Writes ~/plans/camera_poses.json.
"""
import os, json
import numpy as np

PLANS = os.path.expanduser("~/plans")
FRAMES = os.path.expanduser("~/arlo-frames")
CEIL = 9 * 0.3048 + 7 * 0.0254
MAIN_CEIL = 15 * 0.3048 + 4 * 0.0254
F2F = MAIN_CEIL + 0.60

CAMS = {
    "1F_COMMON_AREA": dict(key="common", level="level1"),
    "2F_LOBBY": dict(key="lobby", level="level2"),
    "2F_SW_HALLWAY": dict(key="swhall", level="level2"),
}


def load_ply(p):
    with open(p, "rb") as f:
        h = b""
        while b"end_header\n" not in h:
            h += f.read(1)
        nv = int([l for l in h.decode().splitlines()
                  if l.startswith("element vertex")][0].split()[-1])
        v = np.frombuffer(f.read(nv * 15), dtype=[
            ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
            ("r", "u1"), ("g", "u1"), ("b", "u1")], count=nv)
    return np.stack([v["x"], v["y"], v["z"]], -1).astype(np.float32)


def footprint(xyz):
    """Floor-plane footprint of a camera cloud: extent, area, aspect, yaw."""
    P = np.stack([xyz[:, 0], -xyz[:, 2], xyz[:, 1]], -1)   # X, depth, up
    P[:, 2] -= np.percentile(P[:, 2], 2)
    floor = P[P[:, 2] < 0.65 * CEIL]                       # below head height
    if len(floor) < 500:
        floor = P
    pts = floor[:, :2]
    c = pts.mean(0)
    q = pts - c
    # principal axes -> dominant orientation and extent
    cov = np.cov(q.T)
    w, V = np.linalg.eigh(cov)
    order = np.argsort(-w)
    V = V[:, order]
    proj = q @ V
    lo, hi = np.percentile(proj, 3, axis=0), np.percentile(proj, 97, axis=0)
    ext = hi - lo
    yaw = float(np.degrees(np.arctan2(V[1, 0], V[0, 0])))
    return dict(area=float(ext[0] * ext[1]), major=float(ext[0]),
                minor=float(ext[1]),
                aspect=float(ext[0] / max(ext[1], 1e-3)), yaw=yaw, n=int(len(floor)))


def poly_metrics(poly):
    P = np.asarray(poly, np.float64)
    x, y = P[:, 0], P[:, 1]
    area = 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
    c = P.mean(0)
    q = P - c
    cov = np.cov(q.T)
    w, V = np.linalg.eigh(cov)
    o = np.argsort(-w)
    proj = q @ V[:, o]
    ext = proj.max(0) - proj.min(0)
    return dict(area=float(area), centroid=[float(c[0]), float(c[1])],
                aspect=float(ext[0] / max(ext[1], 1e-3)),
                major=float(ext[0]), minor=float(ext[1]))


def score(fp, rm):
    """Lower is better. Ratio-based so per-camera scale drift is tolerated."""
    ar = fp["area"] / max(rm["area"], 1e-6)
    a_pen = abs(np.log(max(ar, 1e-6)))                 # log-ratio: scale tolerant
    asp = abs(np.log(max(fp["aspect"], 1e-3) / max(rm["aspect"], 1e-3)))
    return 1.0 * a_pen + 0.8 * asp


out = {}
for name, meta in CAMS.items():
    ply = "%s/%s.ply" % (FRAMES, meta["key"])
    if not os.path.exists(ply):
        print("%-16s no cloud, skipped" % name)
        continue
    fp = footprint(load_ply(ply))
    rooms = json.load(open("%s/%s_rooms.json" % (PLANS, meta["level"])))
    cands = []
    for i, r in enumerate(rooms):
        rm = poly_metrics(r["poly"])
        cands.append((score(fp, rm), i, rm))
    cands.sort(key=lambda t: t[0])
    best_s, best_i, best_rm = cands[0]
    out[name] = {
        "level": meta["level"],
        "room_index": best_i,
        "room_id": "%s_room_%02d" % (meta["level"], best_i),
        "position_m": best_rm["centroid"],
        "z_m": 0.0 if meta["level"] == "level1" else F2F,
        "yaw_deg": round(fp["yaw"], 1),
        "match_score": round(float(best_s), 3),
        "confidence": "low" if best_s > 0.9 else ("medium" if best_s > 0.45 else "high"),
        "footprint_area_m2": round(fp["area"], 1),
        "room_area_m2": round(best_rm["area"], 1),
        "runners_up": [{"room": "%s_room_%02d" % (meta["level"], i),
                        "score": round(float(s), 3)} for s, i, _ in cands[1:4]],
    }
    print("%-16s -> %s  score=%.2f (%s)  cam %.0f m2 vs room %.0f m2"
          % (name, out[name]["room_id"], best_s, out[name]["confidence"],
             fp["area"], best_rm["area"]))

json.dump(out, open("%s/camera_poses.json" % PLANS, "w"), indent=1)
print("\nwrote %s/camera_poses.json" % PLANS)
print("NOTE: hypotheses from footprint matching, not calibration. Depth scale")
print("      drifts ~5x across these cameras, so treat 'low' confidence as unplaced.")
