"""2D scan-matching localizer: phone wall-band point cloud -> pose on a floor plan.

Standalone module (does not import phone_slam.py). Given a levelled phone
point cloud and the plan's wall segments, recovers (x, y, theta) by brute
rotation+translation search against a distance-transform of the rasterised
plan walls, which is a far stronger discriminator than area/aspect room
matching: every wall pixel the phone actually saw votes on where it must be
standing, instead of collapsing the whole cloud down to one footprint shape.

Algorithm
---------
1. Rasterise the plan's wall segments once (per distinct `walls` array) onto
   a `res` (default 5cm) grid and run cv2.distanceTransform on the inverted
   wall mask -> DT[row,col] = metres to the nearest wall pixel.
2. Extract the phone cloud's "wall band" (0.9m < z < ceiling-0.3m), project
   to 2D -- these are the observed wall points in the phone's local
   (gravity-levelled, yaw-arbitrary) frame.
3. For each candidate heading theta: rotate the observed points into a
   theta-aligned frame, then evaluate, for EVERY candidate translation at
   once, the sum of DT samples under the rotated points via a shift-and-add
   trick (np.pad the DT map by the cloud's max radial extent with a
   sentinel of `cap` metres, then for each point add the appropriately
   shifted HxW slice of the padded DT into an (H,W) accumulator -- the
   accumulator's argmin over (row,col) is literally the best sensor-origin
   pixel for that theta, in one pass, with no python-level pixel loop).
4. The best (theta, row, col) is the pose estimate. Its accumulator is also
   scanned for secondary local minima (with non-max suppression) and the
   same is done across thetas, to surface multi-modal ambiguity as
   `runners_up` instead of silently picking one hypothesis.
5. confidence in [0,1] combines (a) how good the best score is in absolute
   terms (mean distance-to-wall, small is good) and (b) how much better it
   is than the next distinct peak (small margin = genuinely ambiguous, e.g.
   a symmetric corridor or a blank wall).

`prior` (a previous pose) restricts both the theta sweep and the
translation search window, which is both much faster and much less prone
to snapping to a wrong-but-plausible room -- exactly the tracking regime
the caller (`phone_slam.place()`) runs in after the first fix.
"""
import time
import numpy as np
import cv2

DEFAULT_CEIL = 9 * 0.3048 + 7 * 0.0254   # 2.92 m, matches phone_slam.CEIL
WALL_BAND_LO = 0.9
WALL_BAND_HI_MARGIN = 0.3

_PLAN_CACHE = {}


def _yaw_mat(deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s], [s, c]], np.float64)


def extract_wall_band(obs_points_xyz, ceil_height=DEFAULT_CEIL,
                       lo=WALL_BAND_LO, hi_margin=WALL_BAND_HI_MARGIN):
    """Phone cloud (device/local frame, gravity-levelled, z up) -> 2D wall points."""
    P = np.asarray(obs_points_xyz, np.float64)
    if P.ndim != 2 or P.shape[0] == 0:
        return np.zeros((0, 2))
    z = P[:, 2]
    hi = ceil_height - hi_margin
    band = P[(z > lo) & (z < hi)]
    if len(band) < 30:                    # degenerate cloud: loosen the band
        band = P[z > 0.3]
    return band[:, :2]


def _build_plan_raster(walls, res):
    walls = np.asarray(walls, np.float64)
    xy = walls.reshape(-1, 2)
    pad_m = 1.0
    x0, y0 = xy.min(0) - pad_m
    x1, y1 = xy.max(0) + pad_m
    W = int(np.ceil((x1 - x0) / res)) + 1
    H = int(np.ceil((y1 - y0) / res)) + 1
    img = np.full((H, W), 255, np.uint8)
    for x0s, y0s, x1s, y1s in walls:
        p0 = (int(round((x0s - x0) / res)), int(round((y0s - y0) / res)))
        p1 = (int(round((x1s - x0) / res)), int(round((y1s - y0) / res)))
        cv2.line(img, p0, p1, 0, thickness=2)
    dt = cv2.distanceTransform(img, cv2.DIST_L2, 5).astype(np.float32) * res
    return dict(dt=dt, origin=np.array([x0, y0]), res=res, W=W, H=H)


def _get_plan_raster(walls, res):
    key = (id(walls), np.asarray(walls).shape, round(res, 4))
    plan = _PLAN_CACHE.get(key)
    if plan is None:
        plan = _build_plan_raster(walls, res)
        _PLAN_CACHE.clear()   # keep only the most recent level
        _PLAN_CACHE[key] = plan
    return plan


def _nms_peaks(cands, min_pos=0.5, min_theta=10.0, k=5):
    """cands: list of dict(x,y,theta_deg,score) sorted ascending by score already."""
    kept = []
    for c in cands:
        dup = False
        for kk in kept:
            dth = abs(((c["theta_deg"] - kk["theta_deg"] + 180) % 360) - 180)
            dpos = np.hypot(c["x"] - kk["x"], c["y"] - kk["y"])
            if dpos < min_pos and dth < min_theta:
                dup = True
                break
        if not dup:
            kept.append(c)
        if len(kept) >= k:
            break
    return kept


def localize(obs_points_xyz, walls, base_z=0.0, prior=None,
             theta_step=3.0, res=0.05, ceil_height=DEFAULT_CEIL,
             cap=2.5, prior_radius=2.5, prior_theta_window=20.0,
             prior_theta_step=1.0, max_points=500, top_k=5):
    """Localize a phone wall-band cloud against plan wall segments.

    obs_points_xyz : (N,3) gravity-levelled phone points, local frame, z up
    walls           : (M,4) plan wall segments [x0,y0,x1,y1] in metres
    base_z          : storey floor height (only echoed back, not used in 2D search)
    prior           : optional {'x','y','theta_deg'} previous pose -> tracking mode
    Returns dict(x,y,theta_deg,score,confidence,runners_up,n_points,elapsed_s)
    """
    t0 = time.time()
    pts = extract_wall_band(obs_points_xyz, ceil_height)
    n_obs = len(pts)
    if n_obs < 20:
        return dict(x=None, y=None, theta_deg=None, score=None, confidence=0.0,
                    runners_up=[], n_points=n_obs, base_z=base_z,
                    reason="too few wall-band points", elapsed_s=time.time() - t0)

    if n_obs > max_points:
        idx = np.random.default_rng(0).choice(n_obs, max_points, replace=False)
        pts = pts[idx]
    n = len(pts)

    plan = _get_plan_raster(np.asarray(walls, np.float64), res)
    dt, origin, H, W = plan["dt"], plan["origin"], plan["H"], plan["W"]
    r_max = float(np.hypot(pts[:, 0], pts[:, 1]).max())
    pad = int(np.ceil(r_max / res)) + 2
    dt_c = np.minimum(dt, cap)
    dt_pad = np.pad(dt_c, pad, mode="constant", constant_values=cap)

    if prior is not None and prior.get("x") is not None:
        thetas = np.arange(prior["theta_deg"] - prior_theta_window,
                            prior["theta_deg"] + prior_theta_window + 1e-9,
                            prior_theta_step)
        pc = int(round((prior["x"] - origin[0]) / res))
        pr = int(round((prior["y"] - origin[1]) / res))
        rad = int(np.ceil(prior_radius / res))
        r0, r1 = max(0, pr - rad), min(H, pr + rad + 1)
        c0, c1 = max(0, pc - rad), min(W, pc + rad + 1)
        if r1 <= r0 or c1 <= c0:
            r0, r1, c0, c1 = 0, H, 0, W
    else:
        thetas = np.arange(0.0, 360.0, theta_step)
        r0, r1, c0, c1 = 0, H, 0, W
    h, w = r1 - r0, c1 - c0

    all_peaks = []
    for theta in thetas:
        rp = pts @ _yaw_mat(theta).T
        pr_px = np.round(rp[:, 1] / res).astype(np.int64)
        pc_px = np.round(rp[:, 0] / res).astype(np.int64)
        acc = np.zeros((h, w), np.float32)
        for prx, pcx in zip(pr_px, pc_px):
            rs = pad + prx + r0
            cs = pad + pcx + c0
            acc += dt_pad[rs:rs + h, cs:cs + w]
        # best + a couple of locally non-adjacent runners-up for this theta
        flat = acc.ravel()
        order = np.argsort(flat)[:8]
        local_kept_rc = []
        for idx in order:
            rr, cc = divmod(int(idx), w)
            if any(abs(rr - kr) < 6 and abs(cc - kc) < 6 for kr, kc in local_kept_rc):
                continue
            local_kept_rc.append((rr, cc))
            all_peaks.append(dict(
                x=float(origin[0] + (cc + c0) * res),
                y=float(origin[1] + (rr + r0) * res),
                theta_deg=float(theta % 360.0),
                score=float(flat[idx] / n)))
            if len(local_kept_rc) >= 2:
                break

    all_peaks.sort(key=lambda d: d["score"])
    peaks = _nms_peaks(all_peaks, min_pos=0.5, min_theta=10.0, k=top_k)
    best = peaks[0]
    second = peaks[1] if len(peaks) > 1 else None

    quality = float(np.exp(-best["score"] / 0.25))       # 1.0 at 0m, ~0.37 at 25cm mean err
    if second is not None:
        margin = (second["score"] - best["score"]) / max(second["score"], 1e-6)
        margin = float(np.clip(margin / 0.5, 0.0, 1.0))  # saturate at 50% relative gap
    else:
        margin = 1.0
    confidence = float(np.clip(quality * margin, 0.0, 1.0))

    return dict(x=best["x"], y=best["y"], theta_deg=best["theta_deg"],
                score=best["score"], confidence=confidence,
                runners_up=peaks[1:], n_points=n, base_z=base_z,
                elapsed_s=time.time() - t0)
