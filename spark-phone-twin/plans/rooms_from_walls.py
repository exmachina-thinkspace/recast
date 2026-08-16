"""Derive real rooms from the wall network, replacing the earlier extraction.

The existing room polygons are not rooms. Plotted against the wall segments, a
single 53 m2 "room" on level 2 spans a corridor, covers three separate spaces
and self-intersects; on level 1 the polygons total 557 m2 inside a 2356 m2 floor
plate. Anything built on them — room matching, areas, the scenegraph's room
nodes — inherits that.

A room is the enclosed face of the wall arrangement, so compute it that way:
node every wall segment against every other, then polygonize the planar graph
and keep the bounded faces. This is the standard approach and it cannot produce
a polygon that crosses a wall, because walls are the edges.

  python rooms_from_walls.py [--level level2] [--write]
"""
import argparse, json, os
import numpy as np

PLANS = os.path.expanduser("~/plans")
MIN_AREA = 2.5          # m2 — below this is a closet stub or a sliver
MAX_AREA = 600.0        # m2 — above this is the floor plate, not a room
MIN_THICK = 0.35        # m — reject slivers: area / perimeter is tiny


def load_segments(level):
    w = np.load("%s/%s_walls_m.npy" % (PLANS, level))
    return [((float(a), float(b)), (float(c), float(d))) for a, b, c, d in w]


def rooms_raster(level, res=0.05, door=1.10, write=False):
    """Rooms as connected free space, after closing doorways.

    Polygonizing the wall lines directly fails: a doorway is a gap, so a room
    with a door never closes into a face (level 2 came out at 6% coverage), and
    walls drawn as two parallel lines turn most faces into wall cavities.

    Rasterising instead and morphologically closing the wall mask bridges the
    door gaps, after which each enclosed free-space component IS a room.
    """
    import cv2
    w = np.load("%s/%s_walls_m.npy" % (PLANS, level))
    x0 = min(w[:, 0].min(), w[:, 2].min()) - 1.0
    y0 = min(w[:, 1].min(), w[:, 3].min()) - 1.0
    x1 = max(w[:, 0].max(), w[:, 2].max()) + 1.0
    y1 = max(w[:, 1].max(), w[:, 3].max()) + 1.0
    W = int((x1 - x0) / res); H = int((y1 - y0) / res)
    wall = np.zeros((H, W), np.uint8)
    for sx, sy, ex, ey in w:
        cv2.line(wall, (int((sx - x0) / res), int((sy - y0) / res)),
                 (int((ex - x0) / res), int((ey - y0) / res)), 1, 2)
    k = max(3, int(door / res) | 1)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    closed = cv2.morphologyEx(wall, cv2.MORPH_CLOSE, ker)   # bridge doorways
    free = (closed == 0).astype(np.uint8)
    # drop the exterior: flood from a border pixel
    ff = free.copy()
    mask = np.zeros((H + 2, W + 2), np.uint8)
    cv2.floodFill(ff, mask, (0, 0), 0)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(ff, 4)
    rooms = []
    for i in range(1, n):
        area = float(stats[i, cv2.CC_STAT_AREA]) * res * res
        if area < MIN_AREA or area > MAX_AREA:
            continue
        m = (lab == i).astype(np.uint8)
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        eps = 0.02 * cv2.arcLength(c, True)
        c = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
        if len(c) < 3:
            continue
        poly = [[round(float(px * res + x0), 3), round(float(py * res + y0), 3)]
                for px, py in c]
        rooms.append(dict(area_m2=round(area, 2), area_sqft=round(area * 10.7639, 1),
                          poly=poly))
    rooms.sort(key=lambda r: -r["area_m2"])
    tot = sum(r["area_m2"] for r in rooms)
    plate = (x1 - x0) * (y1 - y0)
    print("%s raster: %d rooms, %.0f m2 (%.0f%% of %.0f m2 plate)"
          % (level, len(rooms), tot, 100 * tot / plate, plate))
    if rooms:
        print("  largest %.1f  median %.1f  smallest %.1f m2"
              % (rooms[0]["area_m2"], rooms[len(rooms) // 2]["area_m2"],
                 rooms[-1]["area_m2"]))
    if write:
        out = "%s/%s_rooms_v2.json" % (PLANS, level)
        json.dump(rooms, open(out, "w"), indent=1)
        print("  wrote %s" % out)
    return rooms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="level2")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--snap", type=float, default=0.06)
    ap.add_argument("--method", default="polygonize", choices=["polygonize", "raster"])
    ap.add_argument("--door", type=float, default=1.10,
                    help="max doorway gap to bridge, metres")
    a = ap.parse_args()

    if a.method == "raster":
        rooms_raster(a.level, door=a.door, write=a.write)
        old_p = "%s/%s_rooms.json" % (PLANS, a.level)
        if os.path.exists(old_p):
            old = json.load(open(old_p))
            print("  previous extraction: %d rooms, %.0f m2"
                  % (len(old), sum(r["area_m2"] for r in old)))
        return 0

    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union, polygonize

    segs = load_segments(a.level)
    print("%s: %d wall segments" % (a.level, len(segs)))

    # snapping endpoints to a grid closes the hairline gaps that stop faces
    # from forming — without it polygonize returns almost nothing
    def snap(p):
        return (round(p[0] / a.snap) * a.snap, round(p[1] / a.snap) * a.snap)

    lines = []
    for p, q in segs:
        sp, sq = snap(p), snap(q)
        if sp != sq:
            lines.append(LineString([sp, sq]))
    print("usable lines after snapping: %d" % len(lines))

    noded = unary_union(lines)          # splits every crossing into shared nodes
    faces = list(polygonize(noded))
    print("polygonized faces: %d" % len(faces))

    rooms = []
    for f in faces:
        if not isinstance(f, Polygon) or f.is_empty:
            continue
        area = float(f.area)
        if area < MIN_AREA or area > MAX_AREA:
            continue
        per = float(f.length)
        if per <= 0 or (area / per) < MIN_THICK / 2.0:
            continue                     # long thin sliver between double walls
        poly = [[round(float(x), 3), round(float(y), 3)]
                for x, y in f.exterior.coords[:-1]]
        rooms.append(dict(area_m2=round(area, 2),
                          area_sqft=round(area * 10.7639, 1),
                          perimeter_m=round(per, 2), poly=poly))
    rooms.sort(key=lambda r: -r["area_m2"])
    tot = sum(r["area_m2"] for r in rooms)
    print("kept %d rooms, total %.0f m2" % (len(rooms), tot))
    if rooms:
        print("  largest %.1f  median %.1f  smallest %.1f m2"
              % (rooms[0]["area_m2"],
                 rooms[len(rooms) // 2]["area_m2"], rooms[-1]["area_m2"]))

    # compare against the previous extraction
    old_p = "%s/%s_rooms.json" % (PLANS, a.level)
    if os.path.exists(old_p):
        old = json.load(open(old_p))
        print("previous: %d rooms, %.0f m2" % (len(old), sum(r["area_m2"] for r in old)))

    # floor plate for context
    w = np.load("%s/%s_walls_m.npy" % (PLANS, a.level))
    plate = ((max(w[:, 0].max(), w[:, 2].max()) - min(w[:, 0].min(), w[:, 2].min())) *
             (max(w[:, 1].max(), w[:, 3].max()) - min(w[:, 1].min(), w[:, 3].min())))
    print("floor plate bbox: %.0f m2 -> rooms cover %.0f%%" % (plate, 100 * tot / plate))

    if a.write:
        out = "%s/%s_rooms_v2.json" % (PLANS, a.level)
        json.dump(rooms, open(out, "w"), indent=1)
        print("wrote %s" % out)
    else:
        print("(dry run — pass --write to save)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
