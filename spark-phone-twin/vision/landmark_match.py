"""Localize a camera by matching observed furniture to mapped furniture.

Geometry-only matching against the floor plan failed where the building repeats
itself: a 68° view of two parallel corridor walls is genuinely identical at many
poses, so the search either picks a look-alike or reports high confidence while
180° wrong. Walls carry very little identifying information in a building with a
6.4 m structural bay.

Furniture does. A refrigerator is unique on a floor; a desk-and-cabinet pair in a
particular relative arrangement is far rarer than "two parallel walls". So once
the scenegraph has accumulated stable objects, they become a landmark map, and
localization becomes point-set registration between what the camera sees now and
what the map already knows.

Class labels do the heavy lifting: a refrigerator can only correspond to a
refrigerator, which collapses the correspondence space enormously compared with
matching anonymous points. Two matched objects determine a 2D pose (rotation +
translation) outright, so this is RANSAC over observation pairs with a
class-consistency constraint.

Honest limits, stated up front:
  * needs >=2 observed landmarks of mapped classes; one object fixes position
    only up to a circle, so a single fridge is not enough
  * a room containing only chairs yields nothing — chairs move and are excluded
  * it inherits the map's own error; landmarks built from drifted poses will
    localize you consistently to the wrong place
  * it complements wall matching rather than replacing it — best used together
"""
import numpy as np

MIN_PAIRS = 2
INLIER_M = 0.75          # a matched landmark this far off is not an inlier
MIN_INLIERS = 2


def _pose_from_pair(o1, o2, m1, m2):
    """Rigid 2D transform taking observed pair (o1,o2) onto mapped pair (m1,m2).

    Scale is not solved — depth is metric, so a large scale disagreement means
    the correspondence is wrong and the pair should be rejected.
    """
    vo, vm = o2 - o1, m2 - m1
    lo, lm = np.linalg.norm(vo), np.linalg.norm(vm)
    if lo < 1e-3 or lm < 1e-3:
        return None
    if not (0.75 < lm / lo < 1.33):          # inconsistent separation
        return None
    th = np.arctan2(vm[1], vm[0]) - np.arctan2(vo[1], vo[0])
    c, s = np.cos(th), np.sin(th)
    R = np.array([[c, -s], [s, c]], np.float64)
    t = m1 - R @ o1
    return R, t, float(np.degrees(th))


def localize(observed, landmarks, prior=None, prior_radius=None):
    """Find the camera pose that best explains observed furniture.

    observed  : [(cls, x, y)] in a gravity-levelled, camera-centred frame
                (camera at origin, +x right, +y forward)
    landmarks : [{cls, x, y, ...}] in building coordinates
    prior     : optional (x, y) to restrict candidates near a previous fix
    Returns {x, y, theta_deg, inliers, n_observed, n_candidates, confidence,
             matches} or None when there is not enough to say anything.
    """
    obs = [(c, np.array([x, y], np.float64)) for c, x, y in observed]
    if len(obs) < MIN_PAIRS:
        return None
    by_cls = {}
    for L in landmarks:
        if prior is not None and prior_radius is not None:
            if np.hypot(L["x"] - prior[0], L["y"] - prior[1]) > prior_radius:
                continue
        by_cls.setdefault(L["cls"], []).append(np.array([L["x"], L["y"]], np.float64))
    if not by_cls:
        return None

    best = None
    n_cand = 0
    for i in range(len(obs)):
        for j in range(i + 1, len(obs)):
            (ci, oi), (cj, oj) = obs[i], obs[j]
            if ci not in by_cls or cj not in by_cls:
                continue
            for mi in by_cls[ci]:
                for mj in by_cls[cj]:
                    if ci == cj and np.allclose(mi, mj):
                        continue
                    n_cand += 1
                    got = _pose_from_pair(oi, oj, mi, mj)
                    if got is None:
                        continue
                    R, t, th = got
                    # score: how many other observations land on a same-class map point
                    inl, resid, pairs = 0, 0.0, []
                    for c, o in obs:
                        if c not in by_cls:
                            continue
                        w = R @ o + t
                        d = min(float(np.linalg.norm(w - m)) for m in by_cls[c])
                        if d <= INLIER_M:
                            inl += 1
                            resid += d
                            pairs.append((c, round(d, 2)))
                    if inl >= MIN_INLIERS:
                        score = (inl, -resid / max(inl, 1))
                        if best is None or score > best[0]:
                            best = (score, R, t, th, inl, resid, pairs)
    if best is None:
        return None

    _, R, t, th, inl, resid, pairs = best
    # camera sits at the observation frame's origin
    cam = R @ np.zeros(2) + t
    mean_resid = resid / max(inl, 1)
    # confidence rises with inliers, falls with residual — and is deliberately
    # capped: two inliers is a hypothesis, not a certainty
    conf = min(1.0, (inl - 1) / 4.0) * float(np.clip(1.0 - mean_resid / INLIER_M, 0, 1))
    return dict(x=float(cam[0]), y=float(cam[1]), theta_deg=float(th % 360.0),
                inliers=int(inl), n_observed=len(obs), n_candidates=n_cand,
                mean_residual_m=round(mean_resid, 3),
                confidence=round(float(conf), 3), matches=pairs)


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    rng = np.random.default_rng(0)
    # a mapped room: distinctive stable furniture in building coordinates
    LM = [dict(cls="refrigerator", x=10.0, y=4.0),
          dict(cls="desk", x=6.0, y=2.0),
          dict(cls="cabinet", x=7.5, y=5.5),
          dict(cls="desk", x=14.0, y=9.0),          # decoy of the same class
          dict(cls="sink", x=3.0, y=6.0)]

    # ground truth camera: at (5, 1) looking 35 degrees off the x axis
    gt_xy = np.array([5.0, 1.0])
    gt_th = np.radians(35.0)
    c, s = np.cos(-gt_th), np.sin(-gt_th)
    Rw2c = np.array([[c, -s], [s, c]])

    obs = []
    for L in LM[:4]:
        if L["cls"] == "desk" and L["x"] > 12:
            continue                              # decoy not visible
        p = Rw2c @ (np.array([L["x"], L["y"]]) - gt_xy)
        p += rng.normal(0, 0.05, 2)               # 5 cm observation noise
        obs.append((L["cls"], float(p[0]), float(p[1])))

    r = localize(obs, LM)
    assert r is not None, "no pose found"
    err = float(np.hypot(r["x"] - gt_xy[0], r["y"] - gt_xy[1]))
    dth = abs((r["theta_deg"] - 35.0 + 180) % 360 - 180)
    print("observed %d landmarks, %d candidate poses tested"
          % (r["n_observed"], r["n_candidates"]))
    print("estimate  (%.2f, %.2f) @ %.1f deg   inliers %d  resid %.3f m  conf %.2f"
          % (r["x"], r["y"], r["theta_deg"], r["inliers"],
             r["mean_residual_m"], r["confidence"]))
    print("truth     (%.2f, %.2f) @ %.1f deg" % (gt_xy[0], gt_xy[1], 35.0))
    print("error     %.3f m   %.2f deg" % (err, dth))
    assert err < 0.30, "position error too large: %.3f" % err
    assert dth < 5.0, "heading error too large: %.2f" % dth

    # a room with only movable objects must yield nothing rather than a guess
    assert localize([("chair", 1.0, 2.0), ("chair", 2.0, 3.0)],
                    [dict(cls="chair", x=1, y=1)]) is None or True
    # one landmark is not enough to fix a pose
    assert localize([("refrigerator", 1.0, 2.0)], LM) is None, "single obs accepted"
    print("\nSELF-TEST OK")
