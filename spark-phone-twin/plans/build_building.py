"""Extrude the approved-plan floor geometry into a 3D building shell.

X,Y  come from the architect's drawing (true metric scale).
Z    (wall height) is measured from the camera depth reconstruction.
"""
import os
import numpy as np

PLANS = os.path.expanduser("~/plans")
FRAMES = os.path.expanduser("~/arlo-frames")


def load_ply_xyz(path):
    with open(path, "rb") as f:
        head = b""
        while b"end_header\n" not in head:
            head += f.read(1)
        txt = head.decode()
        nv = int([l for l in txt.splitlines() if l.startswith("element vertex")][0].split()[-1])
        v = np.frombuffer(f.read(nv * 15), dtype=[
            ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
            ("r", "u1"), ("g", "u1"), ("b", "u1")], count=nv)
    return np.stack([v["x"], v["y"], v["z"]], -1)


# ---- 1. wall height, measured from the cameras ----
heights = {}
for room in ("lobby", "common", "swhall"):
    p = "%s/%s.ply" % (FRAMES, room)
    if not os.path.exists(p):
        continue
    xyz = load_ply_xyz(p)
    y = xyz[:, 1]
    lo, hi = np.percentile(y, 2), np.percentile(y, 98)
    heights[room] = hi - lo
    print("%-7s vertical extent %.2f m  (y %.2f .. %.2f)" % (room, hi - lo, lo, hi))

WALL_H = float(np.median(list(heights.values()))) if heights else 3.0
WALL_H = max(2.4, min(6.0, WALL_H))
print("wall height used: %.2f m  (%.1f ft)" % (WALL_H, WALL_H / 0.3048))

# ---- 2. plan segments -> wall panels ----
for level in ("level1", "level2"):
    f = "%s/%s_segs_m.npy" % (PLANS, level)
    if not os.path.exists(f):
        continue
    s = np.load(f)

    L = np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1])
    ang = np.degrees(np.arctan2(np.abs(s[:, 3] - s[:, 1]), np.abs(s[:, 2] - s[:, 0])))
    # architectural walls are axis-aligned; long diagonals are egress lines
    axis = (ang < 8) | (ang > 82)
    keep = axis & (L > 0.30) & (L < 60)
    w = s[keep]
    print("%s: %d/%d segments kept as walls (%.0f m total run)"
          % (level, len(w), len(s), L[keep].sum()))

    # extrude each segment into a quad
    V, F = [], []
    for x0, y0, x1, y1 in w:
        i = len(V)
        V += [[x0, y0, 0.0], [x1, y1, 0.0], [x1, y1, WALL_H], [x0, y0, WALL_H]]
        F += [[i, i + 1, i + 2], [i, i + 2, i + 3]]
    V = np.array(V, np.float32)
    F = np.array(F, np.int32)

    obj = "%s/%s_walls.obj" % (PLANS, level)
    with open(obj, "w") as fh:
        fh.write("# %s walls, extruded %.2f m, metres, from sheet A-002\n" % (level, WALL_H))
        for v in V:
            fh.write("v %.4f %.4f %.4f\n" % (v[0], v[1], v[2]))
        for t in F + 1:
            fh.write("f %d %d %d\n" % (t[0], t[1], t[2]))
    print("  -> %s  (%d verts, %d tris)" % (obj, len(V), len(F)))
    np.save("%s/%s_walls_m.npy" % (PLANS, level), w)
