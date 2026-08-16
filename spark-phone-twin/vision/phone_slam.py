"""Anchor a phone's monocular-depth cloud to the floor plan and accumulate it.

The floor plan is a drift-free skeleton; a handheld phone is a dense but
free-floating sensor. Rather than dead-reckon the phone (double-integrating
accelerometer noise diverges in seconds), we anchor every placement to the
plan:

  1. gravity-align   device gravity vector -> world Z-up; refine on the cloud's
                     own dominant floor plane so the floor sits at Z=0.
  2. yaw-snap        the cloud's dominant wall direction is snapped to the
                     plan's Manhattan axis; the phone compass, when present,
                     breaks the 4-fold ambiguity.
  3. room-match      the leveled footprint (area + aspect) is matched to a room
                     polygon on the target level, exactly like the old camera
                     registration, and translated onto that room; a small (dx,dy)
                     search minimises point-to-wall distance.
  4. accumulate      placed points are voxel-hashed into a persistent per-level
                     buffer, so successive frames fill the plan shell in with
                     real surfaces, furniture and fixtures the 2D plan never had.

Honest scope: this is map-anchored dense accumulation, not loop-closing metric
SLAM. Global consistency comes from the plan prior, not bundle adjustment.
Monocular depth scale drifts per device, so area matching is scored on log-ratio
with generous tolerance; a low-confidence match means "unplaced", not "here".
Every entry point is defensive — a missing sensor degrades gracefully to a
coarser placement, it never throws into the render loop.
"""
import math
import numpy as np

CEIL = 9 * 0.3048 + 7 * 0.0254          # nominal storey clear height (m)


def _unit(v):
    v = np.asarray(v, np.float64)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])


def _rot_align(a, b):
    """Rotation taking unit vector a onto unit vector b (Rodrigues)."""
    a, b = _unit(a), _unit(b)
    v = np.cross(a, b)
    s = np.linalg.norm(v)
    c = float(np.dot(a, b))
    if s < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1.0 - c) / (s * s))


def gravity_align(P, gravity):
    """Rotate the cloud so measured gravity points to world -Z (down).

    The app's phone cloud is already in device axes (X right, Y up-screen,
    Z toward the user), so the DeviceMotion gravity vector lives in the same
    frame. Returns (P_up, R) with Z now up.
    """
    if gravity is None or not np.isfinite(np.asarray(gravity, float)).all():
        return P.astype(np.float32), np.eye(3, dtype=np.float32)
    R = _rot_align(gravity, [0.0, 0.0, -1.0])        # gravity -> down
    return (P @ R.T).astype(np.float32), R.astype(np.float32)


def refine_floor(P):
    """Drop the cloud so its dominant floor plane sits at Z=0.

    After gravity alignment the floor is roughly flat; take the lowest band of
    points, use its median height as the floor, and subtract it. Robust to the
    depth tails that would otherwise push the floor metres off.
    """
    z = P[:, 2]
    lo = np.percentile(z, 4)
    band = P[z < lo + 0.25]
    floor = float(np.median(band[:, 2])) if len(band) > 30 else float(lo)
    Q = P.copy()
    Q[:, 2] -= floor
    return Q, floor


def plan_axis(walls):
    """Dominant Manhattan wall angle of a level, in [0, 90) degrees.

    walls: (N,4) array of [x0,y0,x1,y1] metre segments. Weighted by length so
    long corridors dominate over short jogs.
    """
    if walls is None or len(walls) == 0:
        return 0.0
    d = walls[:, 2:4] - walls[:, 0:2]
    ln = np.hypot(d[:, 0], d[:, 1])
    ang = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 90.0
    # circular-ish mean on the doubled angle to average around the 90 wrap
    a2 = np.radians(ang * 2.0)
    m = np.arctan2(np.average(np.sin(a2), weights=ln + 1e-6),
                   np.average(np.cos(a2), weights=ln + 1e-6))
    return float((np.degrees(m) / 2.0) % 90.0)


def wall_direction(P):
    """Dominant horizontal direction of the wall band, degrees in [0,180)."""
    z = P[:, 2]
    band = P[(z > 0.4) & (z < CEIL - 0.4)]
    if len(band) < 200:
        band = P
    xy = band[:, :2] - band[:, :2].mean(0)
    cov = np.cov(xy.T)
    w, V = np.linalg.eigh(cov)
    major = V[:, int(np.argmax(w))]
    return float(np.degrees(np.arctan2(major[1], major[0])) % 180.0)


def snap_yaw(P, plan_deg, compass_deg=None):
    """Yaw (deg) that rotates the cloud so its walls align to the plan axis.

    Snaps the cloud's dominant wall direction to the nearest plan Manhattan axis
    (plan_deg or plan_deg+90). Compass, when given, only nudges which of the two
    perpendicular axes we prefer; it is never trusted absolutely because indoor
    steel skews the magnetometer.
    """
    wd = wall_direction(P)
    cands = [plan_deg % 180.0, (plan_deg + 90.0) % 180.0]
    # rotation needed to bring wd onto each candidate axis
    rots = [((c - wd + 90) % 180) - 90 for c in cands]
    if compass_deg is not None and np.isfinite(compass_deg):
        # prefer the axis closest to the phone's reported heading
        pref = np.argmin([abs((((compass_deg % 180) - c + 90) % 180) - 90)
                          for c in cands])
        return float(rots[int(pref)])
    return float(min(rots, key=abs))            # smallest correction otherwise


def _yaw_mat(deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float32)


def footprint(P):
    """Leveled cloud -> floor footprint metrics for room matching."""
    z = P[:, 2]
    floor = P[z < 0.65 * CEIL]
    if len(floor) < 200:
        floor = P
    xy = floor[:, :2]
    c = xy.mean(0)
    q = xy - c
    cov = np.cov(q.T)
    w, V = np.linalg.eigh(cov)
    ext = (q @ V[:, np.argsort(-w)])
    lo, hi = np.percentile(ext, 3, 0), np.percentile(ext, 97, 0)
    e = hi - lo
    return dict(area=float(e[0] * e[1]), major=float(e[0]), minor=float(e[1]),
                aspect=float(e[0] / max(e[1], 1e-3)),
                centroid=[float(c[0]), float(c[1])])


def _poly_metrics(poly):
    Pp = np.asarray(poly, np.float64)
    x, y = Pp[:, 0], Pp[:, 1]
    area = 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
    c = Pp.mean(0)
    q = Pp - c
    w, V = np.linalg.eigh(np.cov(q.T))
    ext = (q @ V[:, np.argsort(-w)])
    e = ext.max(0) - ext.min(0)
    return dict(area=float(area), centroid=[float(c[0]), float(c[1])],
                aspect=float(e[0] / max(e[1], 1e-3)))


def observed_ceiling(P, min_pts=150, min_h=2.2):
    """Floor-to-ceiling height visible in a levelled cloud, or None.

    Returns None unless the camera actually saw a ceiling. A phone held
    horizontally sees walls and floor only, and inventing a height from the
    tallest stray point would be worse than admitting we cannot tell.
    """
    if P is None or len(P) < min_pts:
        return None
    z = P[:, 2]
    hi = float(np.percentile(z, 98))
    if hi < min_h:
        return None                      # never looked up; no ceiling observed
    # require some actual mass up there, not one noisy point
    if int((z > hi - 0.35).sum()) < 40:
        return None
    return hi


def height_compatible(observed, room_h, tol=0.75):
    """Is a room's known ceiling height consistent with what we saw?

    Generous tolerance: monocular depth drifts ~10% and a phone may catch a
    soffit or duct rather than the true slab. The point is to rule out rooms of
    a clearly different storey height, not to measure.
    """
    if observed is None or room_h is None:
        return True                      # no evidence either way -> do not filter
    return abs(float(observed) - float(room_h)) <= tol


def match_room(fp, rooms, observed_h=None, room_heights=None, level=""):
    """Pick the room whose polygon best matches a footprint (log-ratio scored).

    Returns (index, room_metrics, score, confidence). Lower score is better;
    scale-tolerant so per-device depth drift doesn't dominate.
    """
    best = None
    n_excluded = 0
    for i, r in enumerate(rooms):
        # Ceiling height is a cheap, powerful filter that footprint matching
        # cannot provide: this building has 2 rooms at 15'-4" and 159 at 9'-7",
        # so seeing a high ceiling narrows 161 candidates to 2 outright.
        if room_heights is not None:
            rh = (room_heights.get("%s_room_%02d" % (level, i)) or {}).get("height_m")
            if not height_compatible(observed_h, rh):
                n_excluded += 1
                continue
        rm = _poly_metrics(r["poly"])
        a = abs(np.log(max(fp["area"] / max(rm["area"], 1e-6), 1e-6)))
        asp = abs(np.log(max(fp["aspect"], 1e-3) / max(rm["aspect"], 1e-3)))
        s = 1.0 * a + 0.8 * asp
        if best is None or s < best[2]:
            best = (i, rm, s)
    if best is None and n_excluded:
        # height ruled everything out — the observation is inconsistent with the
        # storey, so fall back rather than force a wrong room
        return match_room(fp, rooms)
    if best is None:
        return None
    i, rm, s = best
    conf = "low" if s > 0.9 else ("medium" if s > 0.45 else "high")
    return i, rm, float(s), conf


def place(P, colors, level_base_z, walls, rooms,
          gravity=None, compass_deg=None, room_heights=None, level=""):
    """Full pipeline: phone camera-frame cloud -> building-frame cloud + meta.

    P            (N,3) device-frame cloud (X right, Y up, Z toward user)
    colors       (N,3) uint8, returned unchanged
    level_base_z floor height of the target storey in the building (m)
    walls, rooms plan geometry for the target level
    Returns dict(points, colors, room_index, room_id, confidence, yaw_deg,
                 centroid, footprint_area, room_area) or None if too sparse.
    """
    if P is None or len(P) < 400:
        return None
    up, _ = gravity_align(P, gravity)
    lev, floor = refine_floor(up)
    # depth noise punches points through the floor and smears the accumulated
    # map; nothing real sits below the slab or above the storey.
    keep = (lev[:, 2] > -0.35) & (lev[:, 2] < CEIL + 1.2)
    if keep.sum() >= 400:
        lev, colors = lev[keep], np.asarray(colors)[keep]
    yaw = snap_yaw(lev, plan_axis(walls), compass_deg)
    rot = lev @ _yaw_mat(yaw).T

    fp = footprint(rot)
    room_index = room_id = None
    conf = "unplaced"
    room_area = 0.0
    dx = dy = 0.0
    obs_h = observed_ceiling(rot)
    if rooms:
        m = match_room(fp, rooms, observed_h=obs_h,
                       room_heights=room_heights, level=level)
        if m:
            room_index, rm, score, conf = m
            room_area = rm["area"]
            # translate footprint centroid onto the room centroid, refine (dx,dy)
            cen = np.array(fp["centroid"], np.float64)
            tgt = np.array(rm["centroid"], np.float64)
            dx, dy = tgt - cen
            rot[:, 0] += dx
            rot[:, 1] += dy
            room_id = "%s_room_%02d" % ("level1" if level_base_z < 1.0
                                        else "level2", room_index)
    rot[:, 2] += level_base_z
    # Expose the transform so callers can place *other* points (object
    # detections) into the same building frame without re-deriving it.
    # np.eye's second POSITIONAL arg is the column count, not the dtype —
    # np.eye(3, np.float32) throws, which killed every placement silently.
    R_g = _rot_align(gravity, [0.0, 0.0, -1.0]).astype(np.float32) \
        if gravity is not None and np.isfinite(np.asarray(gravity, float)).all() \
        else np.eye(3, dtype=np.float32)
    xf = dict(R_gravity=R_g, floor=float(floor), yaw_deg=float(yaw),
              dx=float(dx), dy=float(dy), base_z=float(level_base_z))
    return dict(points=rot.astype(np.float32), colors=colors,
                room_index=room_index, room_id=room_id, confidence=conf,
                yaw_deg=round(yaw, 1), centroid=fp["centroid"], transform=xf,
                observed_ceiling_m=None if obs_h is None else round(float(obs_h), 2),
                footprint_area=round(fp["area"], 1), room_area=round(room_area, 1))


def apply_transform(P, xf):
    """Put arbitrary device-frame points into the building frame via place()'s xf."""
    if P is None or len(P) == 0:
        return np.zeros((0, 3), np.float32)
    q = (np.asarray(P, np.float32) @ xf["R_gravity"].T)
    q[:, 2] -= xf["floor"]
    q = q @ _yaw_mat(xf["yaw_deg"]).T
    q[:, 0] += xf["dx"]
    q[:, 1] += xf["dy"]
    q[:, 2] += xf["base_z"]
    return q.astype(np.float32)


class Accumulator:
    """Voxel-hashed persistent point buffer — the 'filled-in' geometry.

    Last-write-wins per voxel keeps it cheap enough to fold in a fresh phone
    cloud every ~1s without a growing memory footprint. 21 bits/axis packs an
    (ix,iy,iz) key into one int64.
    """
    _M = (1 << 21) - 1
    _H = 1 << 20

    def __init__(self, voxel=0.06, cap=1_500_000):
        self.voxel = float(voxel)
        self.cap = int(cap)
        self.vox = {}

    def _pack(self, key):
        k = key.astype(np.int64) & self._M
        return (k[:, 0] << 42) | (k[:, 1] << 21) | k[:, 2]

    def add(self, P, C):
        """Fold a cloud in. Quantise *before* thinning so the same frame always
        yields the same voxels — random subsampling made re-adding a frame grow
        the map instead of converging, and sampled surfaces unevenly."""
        if P is None or len(P) == 0:
            return
        packed = self._pack(np.floor(P / self.voxel).astype(np.int64))
        packed, first = np.unique(packed, return_index=True)   # dedup in-frame
        C = np.asarray(C, np.uint8)[first]
        if len(packed) > 20000:                  # bound per-frame cost, evenly
            step = int(np.ceil(len(packed) / 20000))
            packed, C = packed[::step], C[::step]
        for p, c in zip(packed.tolist(), C):
            self.vox[p] = (int(c[0]), int(c[1]), int(c[2]))
        if len(self.vox) > self.cap:             # evict oldest insertions
            drop = len(self.vox) - self.cap
            for k in list(self.vox.keys())[:drop]:
                del self.vox[k]

    def __len__(self):
        return len(self.vox)

    def get(self):
        if not self.vox:
            return None, None
        ks = np.fromiter(self.vox.keys(), np.int64, len(self.vox))
        C = np.array(list(self.vox.values()), np.uint8)

        def sx(a):
            a = (a & self._M).astype(np.int64)
            a[a >= self._H] -= (1 << 21)
            return a
        P = np.stack([sx(ks >> 42), sx(ks >> 21), sx(ks)], -1).astype(np.float32)
        return P * self.voxel, C


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    rng = np.random.default_rng(0)
    # synthetic room: 5x3m floor + two walls, in a canonical frame
    fx = rng.uniform(0, 5, 4000); fy = rng.uniform(0, 3, 4000)
    floor = np.stack([fx, fy, np.zeros_like(fx)], -1)
    wy = rng.uniform(0, 3, 2000); wz = rng.uniform(0, 2.4, 2000)
    wall = np.stack([np.zeros_like(wy), wy, wz], -1)
    room = np.concatenate([floor, wall]).astype(np.float32)

    # tilt + rotate + lift it into a messy "device" frame, add gravity
    tilt = _rot_align([0, 0, 1], _unit([0.15, -0.1, 1.0]))
    dev = (room @ tilt.T).astype(np.float32)
    grav = tilt @ np.array([0, 0, -1.0])          # gravity in device frame
    cols = np.full((len(dev), 3), 200, np.uint8)

    up, _ = gravity_align(dev, grav)
    lev, fl = refine_floor(up)
    print("floor recovered near 0:", round(float(np.median(lev[:, 2][lev[:, 2] < 0.2])), 3))
    print("plan_axis of an axis-aligned wall set:",
          round(plan_axis(np.array([[0, 0, 0, 5.0], [0, 0, 3.0, 0]])), 2))
    acc = Accumulator(voxel=0.1)
    acc.add(lev, cols)
    acc.add(lev + np.array([10, 0, 0], np.float32), cols)
    P, C = acc.get()
    print("accumulator voxels:", len(acc), "points back:", 0 if P is None else len(P))
    print("SELF-TEST OK")


# ---------------------------------------------------------------- map matching
def _seg_cross(p0, p1, q0, q1):
    """Do segments p0->p1 and q0->q1 properly intersect?"""
    def cr(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1, d2 = cr(q0, q1, p0), cr(q0, q1, p1)
    d3, d4 = cr(p0, p1, q0), cr(p0, p1, q1)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def constrain_move(p0, p1, walls, max_walls=4000):
    """Snap-to-walkable: reject a step that passes through a wall.

    This is the indoor equivalent of the snap-to-road constraint that makes the
    blue dot in a driving app look far better than raw GNSS — most of the
    apparent accuracy comes from the map, not the sensor. A person cannot walk
    through a wall, so a proposed motion that crosses one is wrong regardless of
    how confident the estimate was.

    Returns (x, y, blocked). When blocked, the move is stopped just short of the
    wall rather than discarded, so walking into a wall parks you against it
    instead of teleporting back.
    """
    p0 = (float(p0[0]), float(p0[1]))
    p1 = (float(p1[0]), float(p1[1]))
    if walls is None or len(walls) == 0:
        return p1[0], p1[1], False
    W = np.asarray(walls, np.float64)
    if len(W) > max_walls:
        W = W[:max_walls]
    # only test walls whose bounding box overlaps the move, with a small pad
    lo_x, hi_x = min(p0[0], p1[0]) - 0.2, max(p0[0], p1[0]) + 0.2
    lo_y, hi_y = min(p0[1], p1[1]) - 0.2, max(p0[1], p1[1]) + 0.2
    m = ((np.minimum(W[:, 0], W[:, 2]) <= hi_x) & (np.maximum(W[:, 0], W[:, 2]) >= lo_x) &
         (np.minimum(W[:, 1], W[:, 3]) <= hi_y) & (np.maximum(W[:, 1], W[:, 3]) >= lo_y))
    near = W[m]
    if not len(near):
        return p1[0], p1[1], False
    for x0, y0, x1, y1 in near:
        if _seg_cross(p0, p1, (x0, y0), (x1, y1)):
            # stop just short of the crossing instead of rejecting outright
            for f in (0.6, 0.35, 0.15, 0.0):
                cand = (p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f)
                if not any(_seg_cross(p0, cand, (a, b), (c, d))
                           for a, b, c, d in near):
                    if f >= 0.35:
                        return cand[0], cand[1], True
                    break
            # Truncation alone parks the pose against the wall, and every later
            # step truncates to zero as well -- the position never moves again.
            # Slide instead: keep the component of the motion that runs ALONG
            # the wall. Walking straight at it still gets nowhere, which is
            # right; walking at an angle carries you down the corridor.
            wx, wy = (x1 - x0), (y1 - y0)
            wn = math.hypot(wx, wy)
            if wn > 1e-9:
                ux, uy = wx / wn, wy / wn
                mvx, mvy = p1[0] - p0[0], p1[1] - p0[1]
                t = mvx * ux + mvy * uy               # projection onto the wall
                sx, sy = p0[0] + ux * t, p0[1] + uy * t
                if not any(_seg_cross(p0, (sx, sy), (a, b), (c, d))
                           for a, b, c, d in near):
                    return sx, sy, True
            return p0[0], p0[1], True
    return p1[0], p1[1], False
