"""Render a room from the scenegraph as a depth map, for image-model conditioning.

The first interior renders conditioned on a depth map derived from a fisheye
camera photo. ControlNet did its job faithfully and reproduced the distortion:
recognisable materials, incoherent space. The structural source has to be a
clean perspective view, and we already have one — the 3D model itself.

This puts a virtual camera inside a chosen room at eye height, rasterises the
plan walls plus the scenegraph's object primitives into a z-buffer, and writes
a depth PNG that ControlNet can condition on. Geometry comes from the twin, so
the generated interior keeps the room's real proportions and openings.

  python render_room_depth.py --room level2_room_10 --out ~/plans/room_depth.png
"""
import argparse, json, os, sys
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.expanduser("~/arlo-vision"))
import plan_render

PLANS = os.path.expanduser("~/plans")
CEIL = 9 * 0.3048 + 7 * 0.0254
MAIN_CEIL = 15 * 0.3048 + 4 * 0.0254
F2F = MAIN_CEIL + 0.60
EYE = 1.60


def look_at(eye, target, up=(0, 0, 1)):
    """World -> camera rotation. Camera looks down its own +Z."""
    f = np.asarray(target, np.float64) - np.asarray(eye, np.float64)
    n = np.linalg.norm(f)
    f = f / n if n > 1e-9 else np.array([1.0, 0, 0])
    u = np.asarray(up, np.float64)
    r = np.cross(f, u)
    rn = np.linalg.norm(r)
    if rn < 1e-9:                       # looking straight up/down
        r = np.array([1.0, 0, 0]); rn = 1.0
    r /= rn
    u2 = np.cross(r, f)
    return np.stack([r, u2, f]).astype(np.float32)   # rows = camera axes


def zbuffer(quads, R, eye, hfov_deg, w, h):
    """Rasterise quads into a true per-pixel depth buffer (metres; inf = no hit).

    Per-pixel, not per-quad: each quad defines a plane in camera space and every
    covered pixel gets its own ray-plane intersection depth. A constant depth per
    quad collapses floors and angled walls into flat slabs, which is exactly what
    made the first renders unusable.
    """
    Z = np.full((h, w), np.inf, np.float32)
    if quads is None or len(quads) == 0:
        return Z
    P = (quads.reshape(-1, 3) - np.asarray(eye, np.float32)) @ R.T
    P = P.reshape(-1, 4, 3)
    vis = (P[:, :, 2] > 0.05).any(1)             # keep partially-visible quads
    P = P[vis]
    if not len(P):
        return Z

    f = (w / 2.0) / np.tan(np.radians(hfov_deg) / 2.0)
    uu, vv = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    # ray direction per pixel, normalised so its z-component is 1 -> t == depth
    dx = (uu - w / 2.0) / f
    dy = (h / 2.0 - vv) / f

    for q in P:
        front = q[q[:, 2] > 0.05]
        if len(front) < 3:
            continue
        u = f * q[:, 0] / np.maximum(q[:, 2], 0.05) + w / 2.0
        v = h / 2.0 - f * q[:, 1] / np.maximum(q[:, 2], 0.05)
        poly = np.stack([u, v], -1).astype(np.int32)
        if not np.isfinite(poly).all():
            continue
        mask = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(mask, poly, 1, cv2.LINE_8)
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            continue
        # plane through the quad in camera space
        n = np.cross(q[1] - q[0], q[2] - q[0])
        nn = np.linalg.norm(n)
        if nn < 1e-9:
            continue
        n = n / nn
        denom = n[0] * dx[ys, xs] + n[1] * dy[ys, xs] + n[2]
        ok = np.abs(denom) > 1e-6
        if not ok.any():
            continue
        t = float(np.dot(n, q[0])) / denom[ok]
        ys, xs = ys[ok], xs[ok]
        good = (t > 0.05) & (t < 200.0)
        ys, xs, t = ys[good], xs[good], t[good]
        if len(t) == 0:
            continue
        cur = Z[ys, xs]
        nearer = t < cur
        Z[ys[nearer], xs[nearer]] = t[nearer]
    return Z


def depth_to_png(Z, invert=True):
    """Depth buffer -> 8-bit map. ControlNet depth expects near=bright."""
    m = np.isfinite(Z)
    if not m.any():
        return np.zeros(Z.shape, np.uint8)
    lo, hi = np.percentile(Z[m], 2), np.percentile(Z[m], 98)
    D = np.clip((Z - lo) / max(hi - lo, 1e-6), 0, 1)
    D[~m] = 1.0                                   # unhit = far
    if invert:
        D = 1.0 - D
    return (D * 255).astype(np.uint8)


def _inside(pt, poly):
    P = np.asarray(poly, np.float64)
    x, y = float(pt[0]), float(pt[1])
    inside, j = False, len(P) - 1
    for i in range(len(P)):
        xi, yi, xj, yj = P[i, 0], P[i, 1], P[j, 0], P[j, 1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def interior_point(poly, walls=None):
    """Most-open point inside the polygon, respecting interior walls.

    The room polygons are coarse regions, not wall-bounded rooms: a 53 m2
    "room" on level 2 contains wall segments 0.25 m from its polygon-derived
    centre. Clearance measured against the polygon alone therefore lies, and
    put the camera inside a wall. Rasterising the actual wall segments as
    obstacles before the distance transform fixes it.
    """
    P = np.asarray(poly, np.float64)
    x0, y0 = P[:, 0].min(), P[:, 1].min()
    x1, y1 = P[:, 0].max(), P[:, 1].max()
    res = max((x1 - x0), (y1 - y0)) / 240.0
    w = max(8, int((x1 - x0) / res) + 4)
    h = max(8, int((y1 - y0) / res) + 4)
    m = np.zeros((h, w), np.uint8)
    q = np.stack([(P[:, 0] - x0) / res + 2, (P[:, 1] - y0) / res + 2], -1).astype(np.int32)
    cv2.fillPoly(m, [q], 1)
    if walls is not None and len(walls):
        W = np.asarray(walls, np.float64)
        inb = (((W[:, [0, 2]] >= x0 - 1) & (W[:, [0, 2]] <= x1 + 1)).any(1) &
               ((W[:, [1, 3]] >= y0 - 1) & (W[:, [1, 3]] <= y1 + 1)).any(1))
        for sx, sy, ex, ey in W[inb]:
            cv2.line(m, (int((sx - x0) / res + 2), int((sy - y0) / res + 2)),
                     (int((ex - x0) / res + 2), int((ey - y0) / res + 2)), 0, 2)
    dt = cv2.distanceTransform(m, cv2.DIST_L2, 5)
    iy, ix = np.unravel_index(int(np.argmax(dt)), dt.shape)
    return (float((ix - 2) * res + x0), float((iy - 2) * res + y0),
            float(dt[iy, ix] * res))          # clearance from walls, in metres


def room_camera(poly, base_z, walls=None):
    """Stand at the most-interior point, looking down the room's long axis.

    Backing the camera toward a wall (the previous approach) put the eye inside
    the wall itself, so the render was one flat surface filling the frame.
    """
    P = np.asarray(poly, np.float64)
    cx, cy, clear = interior_point(poly, walls)
    c = np.array([cx, cy], np.float64)
    q = P - P.mean(0)
    w, V = np.linalg.eigh(np.cov(q.T))
    major = V[:, int(np.argmax(w))]
    # step back along the long axis only as far as we can stay well inside
    back = c.copy()
    for frac in (0.55, 0.4, 0.25, 0.1, 0.0):
        cand = c - major * (clear * 2.2 * frac)
        if _inside(cand, poly):
            back = cand
            break
    eye = np.array([back[0], back[1], base_z + EYE], np.float32)
    ahead = back + major * max(clear * 2.0, 3.0)
    tgt = np.array([ahead[0], ahead[1], base_z + EYE * 0.9], np.float32)
    return eye, tgt, clear


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", default="")
    ap.add_argument("--out", default="%s/room_depth.png" % PLANS)
    ap.add_argument("--w", type=int, default=512)
    ap.add_argument("--h", type=int, default=512)
    ap.add_argument("--hfov", type=float, default=75.0)
    ap.add_argument("--sg", default="%s/scenegraph.json" % PLANS)
    a = ap.parse_args()

    room = a.room
    if not room:
        # NOT the biggest — the largest polygon is the floor plate outline, and
        # standing in it renders the whole floor. Pick a room-sized room.
        rooms = json.load(open("%s/level2_rooms.json" % PLANS))
        cand = [(i, r["area_m2"]) for i, r in enumerate(rooms)
                if 15.0 <= r["area_m2"] <= 90.0]
        if not cand:
            cand = [(int(np.argmin([abs(r["area_m2"] - 40) for r in rooms])), 0)]
        i = max(cand, key=lambda t: t[1])[0]
        room = "level2_room_%02d" % i
    lv = room.rsplit("_room_", 1)[0]
    idx = int(room.rsplit("_", 1)[1])
    base_z = 0.0 if lv == "level1" else F2F
    rooms = json.load(open("%s/%s_rooms.json" % (PLANS, lv)))
    if idx >= len(rooms):
        print("no such room %s" % room); return 1
    poly = rooms[idx]["poly"]
    print("room %s  area %.1f m2" % (room, rooms[idx]["area_m2"]))

    walls = np.load("%s/%s_walls_m.npy" % (PLANS, lv))
    # keep only walls near this room — the whole floor would occlude everything
    P = np.asarray(poly, np.float64)
    pad = 1.5
    x0, x1 = P[:, 0].min() - pad, P[:, 0].max() + pad
    y0, y1 = P[:, 1].min() - pad, P[:, 1].max() + pad
    mx = ((walls[:, [0, 2]] >= x0) & (walls[:, [0, 2]] <= x1)).any(1)
    my = ((walls[:, [1, 3]] >= y0) & (walls[:, [1, 3]] <= y1)).any(1)
    near = walls[mx & my]
    print("walls near room: %d of %d" % (len(near), len(walls)))
    Q = [plan_render.wall_quads(near, base_z, CEIL, stride=1)]

    # floor and ceiling slabs give the model a ground plane and a lid
    for z in (base_z, base_z + CEIL):
        Q.append(np.array([[[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]]],
                          np.float32))

    # scenegraph objects, if the twin has any for this room
    nobj = 0
    if os.path.exists(a.sg):
        try:
            sg = json.load(open(a.sg))
            for lvl in sg.get("levels", []):
                for r in lvl.get("rooms", []):
                    if r["room_id"] != room:
                        continue
                    for o in r.get("objects", []):
                        size = OBJ_SIZE.get(o["cls"], (0.4, 0.4, 0.4))
                        Q.append(plan_render.box_quads(o["position"], size))
                        nobj += 1
        except Exception as e:
            print("scenegraph unreadable: %s" % str(e)[:80])
    print("scenegraph objects placed: %d" % nobj)

    quads = np.concatenate(Q)
    eye, tgt, clear = room_camera(poly, base_z, near)
    print("camera at (%.1f, %.1f, %.1f) -> (%.1f, %.1f, %.1f), clearance %.1f m"
          % (*eye, *tgt, clear))
    if not _inside(eye[:2], poly):
        print("WARNING: camera is OUTSIDE the room polygon")
    R = look_at(eye, tgt)
    Z = zbuffer(quads, R, eye, a.hfov, a.w, a.h)
    hit = float(np.isfinite(Z).mean())
    # coverage alone is a useless check: a wall pressed against the lens also
    # covers 100%. Depth *variation* is what says we're seeing a space.
    fin = Z[np.isfinite(Z)]
    spread = float(np.percentile(fin, 95) - np.percentile(fin, 5)) if fin.size else 0.0
    print("depth coverage %.1f%%   depth spread %.2f m   near %.2f  far %.2f"
          % (100 * hit, spread, float(fin.min()) if fin.size else -1,
             float(fin.max()) if fin.size else -1))
    if spread < 0.8:
        print("WARNING: near-flat depth — camera is probably facing/inside a wall")
    cv2.imwrite(a.out, depth_to_png(Z))
    print("wrote %s" % a.out)
    return 0


OBJ_SIZE = {
    "person": (0.50, 0.40, 1.70), "chair": (0.55, 0.55, 0.90),
    "desk": (1.40, 0.70, 0.75), "dining table": (1.60, 0.90, 0.75),
    "couch": (2.00, 0.90, 0.80), "bed": (2.00, 1.50, 0.55),
    "tv": (1.10, 0.08, 0.65), "whiteboard": (1.80, 0.08, 1.20),
    "laptop": (0.35, 0.25, 0.25), "potted plant": (0.45, 0.45, 0.80),
    "cabinet": (1.00, 0.50, 1.10), "shelf": (1.00, 0.35, 1.60),
    "refrigerator": (0.75, 0.70, 1.80), "sink": (0.55, 0.45, 0.25),
    "toilet": (0.40, 0.65, 0.75), "bench": (1.50, 0.45, 0.45),
}

if __name__ == "__main__":
    sys.exit(main())
