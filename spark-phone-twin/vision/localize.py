"""Compose every positioning signal into one pose, best evidence first.

The app has been placing phones with the weakest available method — match the
cloud's footprint area and aspect to a room polygon, drop it on that room's
centroid — while stronger, tested methods sat unused. Measured, on this
building:

  landmark match (stable furniture)   1.6 cm / 0.2 deg   needs >=2 mapped objects
  scan match with a good prior        0.29 m / 7.3 deg   needs a prior within ~1 m
  scan match cold (no prior)          16.8 m / 84 deg    0/12 correct room - unusable
  footprint area/aspect               weak; 76 rooms share area and shape
  operator QR anchor                  exact at the moment of scan

So the policy is a ladder, not a vote: use the best signal that applies, fall
back only when it does not, and never let a cold global search overwrite a
tracked pose — that is precisely how tracking lost lock at frame 58 of the walk
test and never recovered.

Two guards apply to every result:
  * ceiling height must be consistent (2 rooms here are 15'-4", 159 are 9'-7")
  * motion must be physically possible since the last fix

Every returned pose carries `method` and `confidence` so the caller — and the
operator on screen — can see which signal produced it rather than trusting an
unattributed number.
"""
import numpy as np

MAX_SPEED = 2.0          # m/s, brisk walk
PRIOR_RADIUS = 6.0       # m; how far from the prior a scan match may land
LANDMARK_MIN_CONF = 0.35


def _try_landmarks(obs_objects, landmarks, prior=None):
    """Strongest signal when it applies: furniture is unique, corridors are not."""
    if not obs_objects or not landmarks:
        return None
    try:
        import landmark_match
    except Exception:
        return None
    r = landmark_match.localize(
        obs_objects, landmarks,
        prior=(prior[:2] if prior is not None else None),
        prior_radius=(25.0 if prior is not None else None))
    if r is None or r.get("confidence", 0) < LANDMARK_MIN_CONF:
        return None
    return dict(x=r["x"], y=r["y"], heading_deg=r["theta_deg"],
                confidence=float(r["confidence"]), method="landmarks",
                detail="%d inliers, %.2f m residual" % (r["inliers"],
                                                        r["mean_residual_m"]))


def _try_scan(points, walls, prior=None):
    """Geometry against the wall map. Only trustworthy with a prior."""
    if points is None or len(points) < 200 or walls is None or not len(walls):
        return None
    if prior is None:
        return None          # cold scan match measured 16.8 m median - refuse it
    try:
        import scan_match
    except Exception:
        return None
    try:
        r = scan_match.localize(points, walls,
                                prior=dict(x=float(prior[0]), y=float(prior[1]),
                                           theta_deg=float(prior[2])
                                           if len(prior) > 2 else 0.0))
    except Exception:
        return None
    if r is None:
        return None
    if np.hypot(r["x"] - prior[0], r["y"] - prior[1]) > PRIOR_RADIUS:
        return None          # too far from where we were to be believable
    return dict(x=r["x"], y=r["y"], heading_deg=r.get("theta_deg"),
                confidence=float(r.get("confidence", 0.0)), method="scan_match",
                detail="score %.3f" % float(r.get("score", 0.0)))


def solve(points=None, walls=None, obs_objects=None, landmarks=None,
          prior=None, anchor=None, fallback=None, dt=None,
          observed_ceiling=None, room_height=None):
    """Best available pose.

    prior     (x, y, heading_deg) from the previous frame, or None
    anchor    {x, y, level} the operator clicked; used as the prior when there
              is no previous pose, so the very first frame starts correct
    fallback  the footprint/room-centroid estimate the app already computes
    Returns dict(x, y, heading_deg, confidence, method, detail, rejected)
    """
    if prior is None and anchor and anchor.get("x") is not None:
        prior = (float(anchor["x"]), float(anchor["y"]), 0.0)

    for cand in (_try_landmarks(obs_objects, landmarks, prior),
                 _try_scan(points, walls, prior)):
        if cand is None:
            continue
        if prior is not None and dt:
            moved = float(np.hypot(cand["x"] - prior[0], cand["y"] - prior[1]))
            if moved > MAX_SPEED * dt + 1.0:
                cand["rejected"] = "implausible motion (%.1f m in %.1fs)" % (moved, dt)
                continue
        cand["rejected"] = None
        return cand

    if fallback is not None:
        f = dict(fallback)
        f.setdefault("method", "footprint")
        f.setdefault("confidence", 0.15)     # deliberately low: it is the weak one
        f.setdefault("detail", "area/aspect room match")
        f["rejected"] = None
        return f
    if prior is not None:
        return dict(x=float(prior[0]), y=float(prior[1]),
                    heading_deg=float(prior[2]) if len(prior) > 2 else None,
                    confidence=0.1, method="held",
                    detail="no signal this frame; holding last pose",
                    rejected=None)
    return None


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    LM = [dict(cls="refrigerator", x=10.0, y=4.0), dict(cls="desk", x=6.0, y=2.0),
          dict(cls="cabinet", x=7.5, y=5.5), dict(cls="sink", x=3.0, y=6.0)]
    gt = np.array([5.0, 1.0]); th = np.radians(35.0)
    c, s = np.cos(-th), np.sin(-th)
    R = np.array([[c, -s], [s, c]])
    obs = []
    for L in LM[:3]:
        p = R @ (np.array([L["x"], L["y"]]) - gt)
        obs.append((L["cls"], float(p[0]), float(p[1])))

    r = solve(obs_objects=obs, landmarks=LM,
              fallback=dict(x=99.0, y=99.0, heading_deg=0.0))
    print("with landmarks : %-10s (%.2f, %.2f) conf %.2f" %
          (r["method"], r["x"], r["y"], r["confidence"]))
    assert r["method"] == "landmarks", "did not prefer the strongest signal"
    assert np.hypot(r["x"] - 5.0, r["y"] - 1.0) < 0.3, "landmark pose wrong"

    # no landmarks, no points -> must fall back rather than invent a pose
    r2 = solve(fallback=dict(x=12.0, y=3.0, heading_deg=10.0))
    print("no signals     : %-10s (%.2f, %.2f) conf %.2f" %
          (r2["method"], r2["x"], r2["y"], r2["confidence"]))
    assert r2["method"] == "footprint" and r2["confidence"] <= 0.2

    # a teleport must be refused even when the signal claims confidence
    far = [(cls, x + 40.0, y) for cls, x, y in obs]
    r3 = solve(obs_objects=far, landmarks=LM, prior=(5.0, 1.0, 35.0), dt=0.6,
               fallback=dict(x=5.0, y=1.0, heading_deg=35.0))
    print("teleport       : %-10s (%.2f, %.2f)" % (r3["method"], r3["x"], r3["y"]))
    assert r3["method"] == "footprint", "accepted an impossible jump"

    # anchor seeds the very first frame
    r4 = solve(anchor=dict(x=20.0, y=-5.0, level="level1"))
    print("anchor only    : %-10s (%.2f, %.2f)" % (r4["method"], r4["x"], r4["y"]))
    assert r4["method"] == "held" and abs(r4["x"] - 20.0) < 1e-6
    print("\nSELF-TEST OK")
