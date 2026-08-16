"""Turn plan line-work into an actual building model: rooms as volumes.

Pipeline
  1 rasterise wall lines -> occupancy mask
  2 close gaps (doorways) so rooms become sealed cells
  3 connected components of FREE space = rooms; reject too-small/too-large
  4 contour -> simplified room polygon
  5 emit solids: floor slab + ceiling slab + extruded wall faces per room
  6 attach room names from the PDF text layer by centroid proximity
"""
import os, json
import numpy as np
import cv2
import pymupdf

PLANS = os.path.expanduser("~/plans")
PPM = 40.0                       # raster px per metre
CEIL = 9 * 0.3048 + 7 * 0.0254
MAIN_CEIL = 15 * 0.3048 + 4 * 0.0254
F2F = MAIN_CEIL + 0.60
WALL_T = 0.13                    # nominal partition thickness (m)
MIN_AREA, MAX_AREA = 4.0, 400.0  # m^2


def room_labels(page_index, x0m, y0m):
    """Text labels from the sheet, in plan metres."""
    doc = pymupdf.open("%s/6986025-CN Approved Plans.pdf" % PLANS)
    page = doc[1]
    M = page.rotation_matrix
    out = []
    for w in page.get_text("words"):
        p = pymupdf.Point(w[0], w[1]) * M
        q = pymupdf.Point(w[2], w[3]) * M
        cx, cy = (p.x + q.x) / 2, (p.y + q.y) / 2
        out.append((w[4], cx, cy))
    return out


for level, base_z, ceil_h in (("level1", 0.0, MAIN_CEIL), ("level2", F2F, CEIL)):
    f = "%s/%s_walls_m.npy" % (PLANS, level)
    if not os.path.exists(f):
        continue
    w = np.load(f)
    x0, x1 = w[:, [0, 2]].min(), w[:, [0, 2]].max()
    y0, y1 = w[:, [1, 3]].min(), w[:, [1, 3]].max()
    W = int((x1 - x0) * PPM) + 60
    H = int((y1 - y0) * PPM) + 60

    mask = np.zeros((H, W), np.uint8)
    for a, b, c, d in w:
        cv2.line(mask,
                 (int((a - x0) * PPM) + 30, H - 30 - int((b - y0) * PPM)),
                 (int((c - x0) * PPM) + 30, H - 30 - int((d - y0) * PPM)),
                 255, 2)

    # seal doorways / small gaps so rooms close, then thin back
    sealed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8))
    free = cv2.bitwise_not(sealed)
    free = cv2.erode(free, np.ones((3, 3), np.uint8), iterations=1)

    n, lab, stats, cent = cv2.connectedComponentsWithStats(free, 4)
    rooms = []
    for i in range(1, n):
        area_m2 = stats[i, cv2.CC_STAT_AREA] / (PPM ** 2)
        if not (MIN_AREA <= area_m2 <= MAX_AREA):
            continue
        # exterior blob touches the border -> not a room
        x, y, ww, hh = stats[i, :4]
        if x <= 1 or y <= 1 or x + ww >= W - 2 or y + hh >= H - 2:
            continue
        comp = (lab == i).astype(np.uint8) * 255
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        poly = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True).reshape(-1, 2)
        if len(poly) < 3:
            continue
        P = np.stack([(poly[:, 0] - 30) / PPM + x0,
                      (H - 30 - poly[:, 1]) / PPM + y0], -1)
        rooms.append(dict(area=area_m2, poly=P,
                          centroid=[float(cent[i][0]), float(cent[i][1])]))

    rooms.sort(key=lambda r: -r["area"])
    print("%s: %d rooms  total %.0f m2 (%.0f sqft)  largest %.1f m2"
          % (level, len(rooms), sum(r["area"] for r in rooms),
             sum(r["area"] for r in rooms) / 0.09290304,
             rooms[0]["area"] if rooms else 0))

    # ---- emit solid geometry ----
    V, F, groups = [], [], []
    for ri, r in enumerate(rooms):
        P = r["poly"]
        n0 = len(V)
        for (px, py) in P:
            V.append([px, py, base_z])
        for (px, py) in P:
            V.append([px, py, base_z + ceil_h])
        k = len(P)
        # floor + ceiling fans
        for j in range(1, k - 1):
            F.append([n0, n0 + j, n0 + j + 1])
            F.append([n0 + k, n0 + k + j + 1, n0 + k + j])
        # walls
        for j in range(k):
            a, b = n0 + j, n0 + (j + 1) % k
            c2, d2 = a + k, b + k
            F.append([a, b, d2])
            F.append([a, d2, c2])
        groups.append((ri, r["area"], n0))

    obj = "%s/%s_rooms.obj" % (PLANS, level)
    with open(obj, "w") as fh:
        fh.write("# %s  %d rooms  ceiling %.3f m  base z %.3f\n"
                 % (level, len(rooms), ceil_h, base_z))
        for v in V:
            fh.write("v %.4f %.4f %.4f\n" % tuple(v))
        gi = 0
        for i, t in enumerate(F):
            if gi < len(groups) and t[0] == groups[gi][2]:
                fh.write("g room_%02d_%.0fm2\n" % (groups[gi][0], groups[gi][1]))
                gi += 1
            fh.write("f %d %d %d\n" % (t[0] + 1, t[1] + 1, t[2] + 1))
    print("  -> %s  (%d verts, %d tris)" % (obj, len(V), len(F)))

    np.save("%s/%s_rooms.npy" % (PLANS, level),
            np.array([r["area"] for r in rooms], np.float32))
    json.dump([{"area_m2": r["area"], "area_sqft": r["area"] / 0.09290304,
                "poly": r["poly"].tolist()} for r in rooms],
              open("%s/%s_rooms.json" % (PLANS, level), "w"), indent=1)

    # preview
    prev = cv2.cvtColor(sealed, cv2.COLOR_GRAY2BGR)
    prev[sealed > 0] = (60, 60, 60)
    rng = np.random.default_rng(7)
    for r in rooms:
        pts = np.stack([((r["poly"][:, 0] - x0) * PPM + 30).astype(int),
                        (H - 30 - (r["poly"][:, 1] - y0) * PPM).astype(int)], -1)
        col = tuple(int(v) for v in rng.integers(70, 255, 3))
        cv2.fillPoly(prev, [pts], col)
        cv2.polylines(prev, [pts], True, (255, 255, 255), 1)
        cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
        cv2.putText(prev, "%.0f" % (r["area"] / 0.09290304), (int(cx) - 18, int(cy)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite("%s/%s_rooms.png" % (PLANS, level),
                cv2.resize(prev, (min(1700, W), int(H * min(1700, W) / W))))
print("done")
