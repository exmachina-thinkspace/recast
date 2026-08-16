"""Measure ceiling height per room from accumulated phone geometry.

The app currently assumes one constant storey height (2.92 m) for all 161 rooms,
taken from the plan's section note. Real buildings vary — a lobby with exposed
structure, a corridor with a dropped tile ceiling and a service room can differ
by a metre or more on the same floor, and RoomPlan reports the real number.

Measuring it is straightforward once geometry is accumulated: take the points
that fall inside a room's polygon and look at the top of the distribution. Two
cautions shape the implementation:

  * a high percentile, not the max — monocular depth throws sparse points well
    above any real ceiling, and the max would chase them every time
  * require enough points, and report the count — a height from 40 points is a
    guess and should be visibly weaker than one from 4000

Rooms that were never walked report nothing rather than a default, so a missing
measurement is distinguishable from a measured one.
"""
import json, os
import numpy as np

MIN_PTS = 250            # below this a room has not really been observed
CEIL_PCT = 97.0          # top of the distribution, robust to depth outliers
PLAUSIBLE = (2.0, 8.0)   # metres; outside this the measurement is not believable


def point_in_poly(pt, poly):
    P = np.asarray(poly, np.float64)
    x, y = float(pt[0]), float(pt[1])
    inside, j = False, len(P) - 1
    for i in range(len(P)):
        xi, yi, xj, yj = P[i, 0], P[i, 1], P[j, 0], P[j, 1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _mask_in_poly(P, poly):
    """Vectorised point-in-polygon over many points (ray casting)."""
    poly = np.asarray(poly, np.float64)
    x, y = P[:, 0], P[:, 1]
    inside = np.zeros(len(P), bool)
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        cond = ((yi > y) != (yj > y)) & \
               (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi)
        inside ^= cond
        j = i
    return inside


def measure(points, rooms, base_z, level="level1"):
    """Per-room ceiling height from an accumulated cloud.

    points : (N,3) building-frame metres
    rooms  : [{poly: [[x,y],...], area_m2: ...}]
    Returns {room_id: {height_m, points, floor_z, ceiling_z, confidence}}
    """
    out = {}
    if points is None or len(points) < MIN_PTS or not rooms:
        return out
    P = np.asarray(points, np.float32)
    # cheap bbox reject before the per-room ray cast
    for i, r in enumerate(rooms):
        poly = np.asarray(r["poly"], np.float64)
        x0, y0 = poly[:, 0].min(), poly[:, 1].min()
        x1, y1 = poly[:, 0].max(), poly[:, 1].max()
        m = ((P[:, 0] >= x0) & (P[:, 0] <= x1) &
             (P[:, 1] >= y0) & (P[:, 1] <= y1))
        if m.sum() < MIN_PTS:
            continue
        sub = P[m]
        m2 = _mask_in_poly(sub[:, :2], poly)
        if m2.sum() < MIN_PTS:
            continue
        z = sub[m2, 2] - base_z
        floor_z = float(np.percentile(z, 3))
        ceil_z = float(np.percentile(z, CEIL_PCT))
        h = ceil_z - floor_z
        if not (PLAUSIBLE[0] <= h <= PLAUSIBLE[1]):
            continue
        n = int(m2.sum())
        # confidence grows with evidence and saturates; 4000 points is plenty
        conf = float(np.clip(n / 4000.0, 0.0, 1.0))
        out["%s_room_%02d" % (level, i)] = dict(
            height_m=round(h, 2), points=n,
            floor_z=round(floor_z, 2), ceiling_z=round(ceil_z, 2),
            confidence=round(conf, 2))
    return out


SPEC_STANDARD = 9 * 0.3048 + 7 * 0.0254      # 9'-7"  smaller offices
SPEC_MAIN = 15 * 0.3048 + 4 * 0.0254         # 15'-4" main room


def from_spec(rooms, level="level1", main_indices=None, main_min_area=None):
    """Assign the building's stated ceiling heights, tagged as specified.

    The operator supplied these from the drawings: 9'-7" through the smaller
    offices, 15'-4" in the main room. That is real information and better than a
    single constant guessed for all 161 rooms — but it is *specified*, not
    observed, so every entry is marked `source: "spec"` and carries no
    confidence from measurement. A later measured value should override it, and
    the two must stay distinguishable: a number from a drawing and a number from
    a sensor fail in different ways.
    """
    out = {}
    if not rooms:
        return out
    areas = [float(r.get("area_m2", 0.0)) for r in rooms]
    if main_indices is None:
        if main_min_area is not None:
            main_indices = {i for i, a in enumerate(areas) if a >= main_min_area}
        else:
            main_indices = {int(np.argmax(areas))} if areas else set()
    for i, r in enumerate(rooms):
        is_main = i in main_indices
        h = SPEC_MAIN if is_main else SPEC_STANDARD
        out["%s_room_%02d" % (level, i)] = dict(
            height_m=round(float(h), 2), points=0,
            floor_z=0.0, ceiling_z=round(float(h), 2),
            confidence=None, source="spec",
            basis="15'-4\" main room" if is_main else "9'-7\" standard",
            area_m2=round(areas[i], 1))
    return out


def merge(spec, measured):
    """Measured beats specified, per room; provenance preserved either way."""
    out = dict(spec)
    for k, v in (measured or {}).items():
        v = dict(v)
        v.setdefault("source", "measured")
        if k in out:
            v["spec_height_m"] = out[k]["height_m"]
        out[k] = v
    return out


def save(all_rooms, path=None, assumed=None):
    """Write ~/plans/ceiling_heights.json, recording what was assumed before."""
    path = path or os.path.expanduser("~/plans/ceiling_heights.json")
    hs = [v["height_m"] for v in all_rooms.values()]
    meas = [v for v in all_rooms.values() if v.get("source") != "spec"]
    doc = dict(
        rooms=all_rooms, total_rooms=len(all_rooms),
        measured_rooms=len(meas), specified_rooms=len(all_rooms) - len(meas),
        assumed_height_m=assumed,
        median_height_m=round(float(np.median(hs)), 2) if hs else None,
        note="source=spec are the drawings' stated heights (9'-7\" standard, "
             "15'-4\" main room); source=measured come from observed points and "
             "override spec for that room")
    tmp = path + ".tmp"
    json.dump(doc, open(tmp, "w"), indent=1)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    rng = np.random.default_rng(0)
    rooms = [dict(area_m2=20.0, poly=[[0, 0], [5, 0], [5, 4], [0, 4]]),
             dict(area_m2=12.0, poly=[[6, 0], [9, 0], [9, 4], [6, 4]])]

    def slab(x0, x1, y0, y1, h, n=3000):
        x = rng.uniform(x0, x1, n); y = rng.uniform(y0, y1, n)
        z = np.where(rng.random(n) < 0.5, rng.normal(0.0, 0.02, n),
                     rng.normal(h, 0.02, n))
        return np.stack([x, y, z], -1).astype(np.float32)

    # room 0 is 2.7 m, room 1 is 4.2 m; plus a few absurd outlier points
    P = np.concatenate([slab(0.2, 4.8, 0.2, 3.8, 2.70),
                        slab(6.2, 8.8, 0.2, 3.8, 4.20),
                        np.array([[2.0, 2.0, 19.0], [7.0, 2.0, 25.0]], np.float32)])
    got = measure(P, rooms, base_z=0.0, level="level1")
    for k, v in sorted(got.items()):
        print("%-18s %.2f m  (%d pts, conf %.2f)"
              % (k, v["height_m"], v["points"], v["confidence"]))
    assert abs(got["level1_room_00"]["height_m"] - 2.70) < 0.10, "room0 height off"
    assert abs(got["level1_room_01"]["height_m"] - 4.20) < 0.10, "room1 height off"

    # an unobserved room must be absent, not defaulted
    sparse = measure(P[:50], rooms, 0.0)
    assert sparse == {}, "reported a height from too few points"
    print("\noutliers at 19 m and 25 m correctly ignored")
    print("SELF-TEST OK")
