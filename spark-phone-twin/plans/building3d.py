"""Two-level building shell at true ceiling height, plus a 3D preview render."""
import os
import numpy as np
import cv2

PLANS = os.path.expanduser("~/plans")
CEIL = 9 * 0.3048 + 7 * 0.0254        # 9'-7" = 2.9210 m
FLOOR_TO_FLOOR = CEIL + 0.60          # + slab/plenum (assumption)

print("ceiling height   %.4f m (9'-7\")" % CEIL)
print("floor-to-floor   %.4f m (assumed 0.60 m slab/plenum)" % FLOOR_TO_FLOOR)

levels = {}
for name, z0 in (("level1", 0.0), ("level2", FLOOR_TO_FLOOR)):
    f = "%s/%s_walls_m.npy" % (PLANS, name)
    if not os.path.exists(f):
        continue
    w = np.load(f)
    levels[name] = (w, z0)
    V, F = [], []
    for x0, y0, x1, y1 in w:
        i = len(V)
        V += [[x0, y0, z0], [x1, y1, z0], [x1, y1, z0 + CEIL], [x0, y0, z0 + CEIL]]
        F += [[i, i + 1, i + 2], [i, i + 2, i + 3]]
    V = np.array(V, np.float32)
    with open("%s/%s_walls.obj" % (PLANS, name), "w") as fh:
        fh.write("# %s  ceiling %.4f m  base z=%.3f  metres\n" % (name, CEIL, z0))
        for v in V:
            fh.write("v %.4f %.4f %.4f\n" % tuple(v))
        for t in np.array(F) + 1:
            fh.write("f %d %d %d\n" % tuple(t))
    print("%s: %d walls -> %d verts, base z=%.2f" % (name, len(w), len(V), z0))

# ---- perspective preview of the stacked building ----
OUT_W, OUT_H = 1600, 950
allpts = []
for name, (w, z0) in levels.items():
    for x0, y0, x1, y1 in w:
        allpts += [[x0, y0, z0], [x1, y1, z0 + CEIL]]
allpts = np.array(allpts, np.float32)
C = allpts.mean(0)
span = float(np.linalg.norm(allpts.max(0) - allpts.min(0)))


def render(yaw, pitch, fname):
    ry, rx = np.radians(yaw), np.radians(pitch)
    Rz = np.array([[np.cos(ry), -np.sin(ry), 0], [np.sin(ry), np.cos(ry), 0], [0, 0, 1]], np.float32)
    Rx = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]], np.float32)
    R = Rx @ Rz
    cam = np.array([0, 0, span * 0.85], np.float32)
    f = OUT_W / 1.5
    img = np.full((OUT_H, OUT_W, 3), 18, np.uint8)

    def proj(p):
        q = (p - C) @ R.T + cam
        if q[2] <= 0.1:
            return None
        return (int(f * q[0] / q[2] + OUT_W / 2), int(OUT_H / 2 - f * q[1] / q[2]))

    for name, (w, z0) in levels.items():
        col = (120, 200, 255) if name == "level1" else (255, 190, 120)
        for x0, y0, x1, y1 in w:
            for za, zb in ((z0, z0), (z0 + CEIL, z0 + CEIL)):
                a = proj(np.array([x0, y0, za], np.float32))
                b = proj(np.array([x1, y1, zb], np.float32))
                if a and b:
                    cv2.line(img, a, b, col, 1, cv2.LINE_AA)
        # vertical studs every Nth wall so the extrusion reads as 3D
        for x0, y0, x1, y1 in w[::9]:
            a = proj(np.array([x0, y0, z0], np.float32))
            b = proj(np.array([x0, y0, z0 + CEIL], np.float32))
            if a and b:
                cv2.line(img, a, b, col, 1, cv2.LINE_AA)

    cv2.putText(img, "1700 Westlake Ave N - Lake Union Building", (18, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (240, 240, 240), 2)
    cv2.putText(img, "Level 1 (blue) + Level 2 (orange)   ceiling 9'-7\" = 2.921 m   from sheet A-002",
                (18, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (170, 170, 170), 1)
    cv2.imwrite(fname, img)
    return fname


tiles = [render(y, p, "%s/bld_%d.png" % (PLANS, i))
         for i, (y, p) in enumerate([(35, 62), (0, 90), (70, 45), (200, 60)])]
grid = np.vstack([np.hstack([cv2.imread(tiles[0]), cv2.imread(tiles[1])]),
                  np.hstack([cv2.imread(tiles[2]), cv2.imread(tiles[3])])])
cv2.imwrite("%s/building_views.jpg" % PLANS, cv2.resize(grid, (1900, 1128)),
            [cv2.IMWRITE_JPEG_QUALITY, 88])
print("wrote building_views.jpg")
