"""Monte Carlo Localization (particle filter) on the floor plan.

Why this exists: the single-hypothesis localizer (scan_match.localize(),
run in a streaming loop by phone_slam.place()) always commits to ONE (x, y,
theta) per frame and searches only a small window around its OWN previous
estimate on the next frame. When that one hypothesis is wrong -- e.g. a
symmetric corridor makes two headings 180deg apart score almost identically
-- there is nothing else alive to fall back on: the tracker snaps to the
wrong mode and then keeps re-confirming itself, because its own bad
estimate is the prior for the next search window. Measured on
prove_tracking.py's 116-frame walk this is exactly what happens: solid
tracking for ~53 frames, then lost lock at frame 58, never recovered, and
reported confidence *anti-correlated* with actual error (r=-0.25) because
the single-hypothesis "confidence" measures how much better the winning
mode beat the runner-up in ONE search, not whether the winning mode is
actually right.

A particle filter fixes this by keeping N competing pose hypotheses alive
simultaneously instead of one. Wrong-heading particles keep existing right
alongside correct ones; they don't get discarded until the *evidence*
(a wall crossing, a bad scan-match score, accumulated over several frames)
kills them off. This module deliberately does NOT reimplement the two hard
parts that already exist and work:

  - the observation likelihood: scan_match.py already turns the plan into a
    distance-transform raster (`_get_plan_raster`) and scores a point cloud
    against it. We reuse that exact raster and sampling idea (imported, not
    rewritten) instead of re-deriving a wall-distance metric.
  - the walkability constraint: phone_slam.constrain_move(p0, p1, walls)
    already knows how to reject/clip a motion that crosses a wall. We call
    it per-particle in the motion model instead of re-deriving segment
    intersection.

What's genuinely new here is the particle-filter machinery around them:
per-particle motion with independent heading noise (so the ensemble can
represent "maybe I turned the wrong way in that corridor" as a live
sub-population instead of a single committed guess), vectorized
measurement scoring of all particles against the DT raster at once,
low-variance resampling gated on effective sample size, augmented-MCL
random-particle injection for recovery, and a confidence number derived
from *how spread out the belief is* rather than how the winner beat the
runner-up in a single frame.

    from mcl import ParticleFilter
    pf = ParticleFilter(walls, n=600)
    pf.reset(x=10.2, y=-22.6, theta_deg=180.0, spread=0.5)
    for each new frame:
        pf.predict(dx=step_forward_m, dy=0.0, dtheta=turn_deg, noise=...)
        pf.update(obs_points_xyz)          # obs in the local sensor frame
        est = pf.estimate()                # {x, y, theta_deg, confidence, spread_m, n_effective}
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan_match as sm            # noqa: E402  (reused: DT raster + likelihood idea)
from phone_slam import constrain_move  # noqa: E402  (reused: wall-crossing rejection)


class ParticleFilter:
    """Floor-plan-constrained Monte Carlo localizer.

    particles: (n, 3) array of [x, y, theta_deg] hypotheses.
    weights:   (n,) normalized importance weights.
    """

    def __init__(self, walls, n=600, res=0.05, rng=None):
        self.walls = np.asarray(walls, np.float64)
        self.n = int(n)
        self.res = float(res)
        self.rng = rng if rng is not None else np.random.default_rng()

        # Reuse scan_match's own plan raster/distance-transform builder --
        # this is the exact same DT grid scan_match.localize() scores
        # against, just fetched once and sampled directly instead of
        # re-running scan_match's grid search per particle.
        self.plan = sm._get_plan_raster(self.walls, self.res)

        xy = self.walls.reshape(-1, 2)
        self._bounds = (float(xy[:, 0].min()), float(xy[:, 0].max()),
                         float(xy[:, 1].min()), float(xy[:, 1].max()))

        self.particles = np.zeros((self.n, 3), np.float64)
        self.weights = np.full(self.n, 1.0 / self.n)

        # Augmented-MCL (Thrun et al.) short/long running-average likelihood,
        # used to decide how many random ("lost fix") particles to inject.
        self.alpha_slow = 0.02
        self.alpha_fast = 0.4
        self.w_slow = 0.0
        self.w_fast = 0.0

        self._neff_last_update = float(self.n)   # ESS at last measurement, pre-resample
        self._last_quality = 0.0
        self.reset()   # default: uniform/global particle cloud over the whole plan

    # ------------------------------------------------------------ reset
    def reset(self, x=None, y=None, theta_deg=None, spread=2.0, theta_spread=180.0):
        """(Re)seed the particle set.

        x=None -> uniform ("global") reinitialization over the plan's
        bounding box -- used when there is no prior at all. Otherwise a
        Gaussian cloud of the given position/heading spread around
        (x, y, theta_deg).
        """
        n = self.n
        x0, x1, y0, y1 = self._bounds
        if x is None:
            xs = self.rng.uniform(x0, x1, n)
            ys = self.rng.uniform(y0, y1, n)
            ths = self.rng.uniform(0.0, 360.0, n)
        else:
            xs = np.clip(x + self.rng.normal(0, spread, n), x0 - 1, x1 + 1)
            ys = np.clip(y + self.rng.normal(0, spread, n), y0 - 1, y1 + 1)
            base_th = theta_deg if theta_deg is not None else 0.0
            ths = (base_th + self.rng.normal(0, theta_spread, n)) % 360.0
        self.particles = np.column_stack([xs, ys, ths])
        self.weights = np.full(n, 1.0 / n)
        self.w_slow = self.w_fast = 0.0
        self._neff_last_update = float(n)

    # ------------------------------------------------------------ motion
    def predict(self, dx, dy, dtheta, noise=None):
        """Motion model: (dx, dy) is a BODY-FRAME displacement (dx=forward,
        dy=lateral, matching PDRTracker's step -- see pdr.py) and dtheta is
        the heading CHANGE reported since the last call (PDR's gyro-derived
        turn). Each particle applies this using its OWN current heading,
        not a single shared heading -- that is what lets the ensemble carry
        a forked "maybe I actually turned the other way" hypothesis instead
        of collapsing to whatever PDR's point estimate says.

        Particles whose proposed motion crosses a wall (checked via
        phone_slam.constrain_move, not reimplemented here) are clipped to
        the wall and heavily downweighted -- a real person cannot walk
        through a wall, so that hypothesis is now much less credible.
        """
        noise = noise or {}
        sigma_fwd = float(noise.get("fwd_m", 0.03)) + float(noise.get("fwd_frac", 0.12)) * abs(dx)
        sigma_lat = float(noise.get("lat_m", 0.03))
        sigma_theta = float(noise.get("theta_deg", 4.0))

        n = self.n
        dtheta_noisy = dtheta + self.rng.normal(0, sigma_theta, n)
        new_theta = (self.particles[:, 2] + dtheta_noisy) % 360.0

        dx_n = dx + self.rng.normal(0, sigma_fwd, n)
        dy_n = dy + self.rng.normal(0, sigma_lat, n)
        rad = np.radians(new_theta)
        c, s = np.cos(rad), np.sin(rad)
        wdx = dx_n * c - dy_n * s
        wdy = dx_n * s + dy_n * c

        old_xy = self.particles[:, :2]
        prop_xy = old_xy + np.column_stack([wdx, wdy])

        xs_out = np.empty(n)
        ys_out = np.empty(n)
        blocked_factor = np.ones(n)
        for i in range(n):
            xo, yo, blocked = constrain_move(old_xy[i], prop_xy[i], self.walls)
            xs_out[i] = xo
            ys_out[i] = yo
            if blocked:
                blocked_factor[i] = 0.08   # heavy reweight ("kill"), not an outright drop

        self.particles[:, 0] = xs_out
        self.particles[:, 1] = ys_out
        self.particles[:, 2] = new_theta
        self.weights *= blocked_factor
        wsum = self.weights.sum()
        self.weights = self.weights / wsum if wsum > 1e-300 else np.full(n, 1.0 / n)

    # -------------------------------------------------------- measurement
    def update(self, obs_points, max_points=250, sigma=0.25):
        """Reweight every particle by scan_match's distance-transform
        likelihood: rotate+translate the observed wall-band points into
        each particle's hypothesized world frame, sample the SAME DT raster
        scan_match.localize() uses, and convert mean distance-to-wall to a
        weight with the identical exp(-score/0.25) form scan_match uses for
        its own `quality` term. This is the reused likelihood; nothing
        about the raster or the score->quality mapping is re-derived here.

        obs_points: (N,3) local-frame phone cloud (gravity-levelled,
        yaw-arbitrary -- same convention as scan_match.localize's
        obs_points_xyz) or already-extracted (N,2) local wall-band points.
        """
        P = np.asarray(obs_points, np.float64)
        if P.ndim != 2 or len(P) == 0:
            return                                    # no observation this cycle
        if P.shape[1] >= 3:
            P = sm.extract_wall_band(P)
        if len(P) == 0:
            return
        if len(P) > max_points:
            idx = self.rng.choice(len(P), max_points, replace=False)
            P = P[idx]

        dt, origin, res = self.plan["dt"], self.plan["origin"], self.plan["res"]
        H, W = self.plan["H"], self.plan["W"]
        cap = 2.5   # matches scan_match.localize's `cap` default

        thetas = np.radians(self.particles[:, 2])
        c, s = np.cos(thetas), np.sin(thetas)
        xr = P[None, :, 0] * c[:, None] - P[None, :, 1] * s[:, None]
        yr = P[None, :, 0] * s[:, None] + P[None, :, 1] * c[:, None]
        wx = xr + self.particles[:, 0:1]
        wy = yr + self.particles[:, 1:2]
        col = np.round((wx - origin[0]) / res).astype(np.int64)
        row = np.round((wy - origin[1]) / res).astype(np.int64)
        inb = (row >= 0) & (row < H) & (col >= 0) & (col < W)
        rr = np.clip(row, 0, H - 1)
        cc = np.clip(col, 0, W - 1)
        d = np.where(inb, dt[rr, cc], cap)
        mean_d = d.mean(axis=1)

        quality = np.exp(-mean_d / sigma)             # same functional form as scan_match's `quality`
        self.weights *= quality
        wsum = self.weights.sum()
        if wsum < 1e-300:
            self.weights = np.full(self.n, 1.0 / self.n)
        else:
            self.weights /= wsum

        w_avg = float(quality.mean())
        self.w_slow += self.alpha_slow * (w_avg - self.w_slow)
        self.w_fast += self.alpha_fast * (w_avg - self.w_fast)
        self._last_quality = w_avg
        self._neff_last_update = self.n_eff()          # ESS BEFORE resampling -- this is
                                                         # what makes confidence mean something
        self.resample()

    # -------------------------------------------------------- resampling
    def n_eff(self):
        return float(1.0 / np.sum(self.weights ** 2))

    def resample(self, ess_frac_trigger=0.5, force=False):
        """Augmented-MCL resample: low-variance/systematic draw from the
        current weights, PLUS a fraction of freshly-injected random
        particles when the running short-term average likelihood (w_fast)
        has dropped well below the long-term average (w_slow) -- i.e. the
        belief has been scoring badly for a while, the classic signature of
        a lost fix. Half of the injected particles are seeded near the
        current best estimate (covers "briefly confused, still nearby") and
        half are fully global over the plan (covers "completely lost") --
        this is the recovery mechanism: a mode that dies can be replaced by
        a fresh one instead of the filter being stuck.
        """
        n = self.n
        neff = self.n_eff()
        p_random = max(0.0, 1.0 - self.w_fast / max(self.w_slow, 1e-9))
        p_random = float(np.clip(p_random, 0.0, 0.3))
        if not force and neff >= ess_frac_trigger * n and p_random < 0.02:
            return False   # belief is healthy and unimodal enough -- skip this cycle

        n_random = int(round(p_random * n))
        n_keep = n - n_random

        new_particles = np.empty((n, 3))
        if n_keep > 0:
            positions = (self.rng.uniform() + np.arange(n_keep)) / n_keep
            cumsum = np.cumsum(self.weights)
            cumsum[-1] = 1.0
            idx = np.searchsorted(cumsum, positions)
            new_particles[:n_keep] = self.particles[idx]

        if n_random > 0:
            x0, y0 = self._weighted_mean()
            half = n_random // 2
            bx0, bx1, by0, by1 = self._bounds
            lx = np.clip(x0 + self.rng.normal(0, 3.0, half), bx0 - 1, bx1 + 1)
            ly = np.clip(y0 + self.rng.normal(0, 3.0, half), by0 - 1, by1 + 1)
            lt = self.rng.uniform(0, 360, half)
            rest = n_random - half
            gx = self.rng.uniform(bx0, bx1, rest)
            gy = self.rng.uniform(by0, by1, rest)
            gt = self.rng.uniform(0, 360, rest)
            new_particles[n_keep:n_keep + half] = np.column_stack([lx, ly, lt])
            new_particles[n_keep + half:] = np.column_stack([gx, gy, gt])

        self.particles = new_particles
        self.weights = np.full(n, 1.0 / n)
        return True

    def _weighted_mean(self):
        w = self.weights
        return (float(np.sum(w * self.particles[:, 0])),
                float(np.sum(w * self.particles[:, 1])))

    # -------------------------------------------------------- estimate
    def estimate(self):
        """Weighted pose estimate + a confidence that is LOW precisely when
        the belief is multi-modal or spread out, regardless of how good any
        single particle's score looked -- the specific failure mode being
        fixed (single-hypothesis confidence that doesn't track being wrong).

        n_effective is measured at the moment of the last measurement
        update, BEFORE resampling resets weights to uniform (resampling
        would otherwise make ESS trivially == n every cycle and hide
        exactly the signal we want).
        """
        w = self.weights
        x = float(np.sum(w * self.particles[:, 0]))
        y = float(np.sum(w * self.particles[:, 1]))
        th = np.radians(self.particles[:, 2])
        sin_m = float(np.sum(w * np.sin(th)))
        cos_m = float(np.sum(w * np.cos(th)))
        theta_deg = float(np.degrees(np.arctan2(sin_m, cos_m)) % 360.0)

        spread_m = float(np.sqrt(np.sum(w * ((self.particles[:, 0] - x) ** 2 +
                                              (self.particles[:, 1] - y) ** 2))))
        neff_frac = self._neff_last_update / self.n
        confidence = float(np.clip(neff_frac * np.exp(-spread_m / 1.5), 0.0, 1.0))

        return dict(x=x, y=y, theta_deg=theta_deg, confidence=confidence,
                    spread_m=spread_m, n_effective=self._neff_last_update)


# ========================================================================= #
# Validation: reuse prove_tracking.py's own 116-frame benchmark verbatim.
# ========================================================================= #
def _synthesize_pdr_stream(frame_t, heading_deg_arr, hz=50, cadence_hz=1.8, seed=7):
    """Build a synthetic phone accel+gyro stream that, fed through the real
    pdr.PDRTracker, reports roughly the ground-truth heading/speed profile
    -- but with real gyro-integration bias drift and real ~0.7m/step
    quantization, i.e. genuine PDR error characteristics, not the ground
    truth itself. This is the motion-model *input*; prove_tracking's own
    raycast observation model is untouched on the measurement side.
    """
    rng = np.random.default_rng(seed)
    dt = 1.0 / hz
    t_end = float(frame_t[-1]) + 0.25
    ts = np.arange(0.0, t_end, dt)
    head_unw = np.unwrap(np.radians(np.interp(ts, frame_t, heading_deg_arr)))
    true_rate = np.degrees(np.gradient(head_unw, dt))       # deg/s

    bias = np.zeros(len(ts))
    for i in range(1, len(ts)):
        bias[i] = 0.995 * bias[i - 1] + rng.normal(0, 0.06)  # slow gyro-bias walk
    rr_alpha = true_rate + bias + rng.normal(0, 2.5, len(ts))

    period = 1.0 / cadence_hz
    phase = (ts % period) / period
    gait = 4.0 * np.exp(-0.5 * ((phase - 0.3) / 0.12) ** 2) + rng.normal(0, 0.25, len(ts))
    comp = gait / np.sqrt(3.0)

    return [dict(t=float(ts[i]), ax=float(comp[i]), ay=float(comp[i]), az=float(comp[i]),
                 rr_alpha=float(rr_alpha[i])) for i in range(len(ts))]


def _lost_and_recovered(pos_err, lost_thresh=3.0, run=5, recover_thresh=1.5):
    """A frame is 'lost' once pos_err (or NaN) stays above lost_thresh for
    >= `run` consecutive frames. 'Recovered' if pos_err later drops below
    recover_thresh and stays there for >= `run` consecutive frames."""
    bad = np.isnan(pos_err) | (pos_err > lost_thresh)
    lost_at = None
    for i in range(len(bad) - run + 1):
        if bad[i:i + run].all():
            lost_at = i
            break
    if lost_at is None:
        return None, None
    good = (~np.isnan(pos_err)) & (pos_err < recover_thresh)
    recovered_at = None
    for i in range(lost_at, len(good) - run + 1):
        if good[i:i + run].all():
            recovered_at = i
            break
    return lost_at, recovered_at


def _run_benchmark():
    import pickle
    import time as _time

    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    print("[mcl] importing prove_tracking (runs + saves the baseline benchmark)...")
    import prove_tracking as pt   # noqa: E402  -- executes the SAME 116-frame walk/benchmark

    walls = pt.walls
    traj_xy = pt.traj_xy
    heading_smooth = pt.heading_smooth
    n_frames = pt.n_frames
    room_label = pt.room_label
    raycast_frame = pt.raycast_frame
    baseline_records = pt.records

    frame_t = np.arange(n_frames) * pt.DT

    from pdr import PDRTracker
    imu = _synthesize_pdr_stream(frame_t, heading_smooth)
    imu_by_frame = [[] for _ in range(n_frames)]
    j = 0
    for smp in imu:
        while j + 1 < n_frames and smp["t"] >= frame_t[j + 1]:
            j += 1
        imu_by_frame[j].append(smp)

    trk = PDRTracker(step_length_m=0.7, method="fixed")
    trk.set_anchor(x=float(traj_xy[0, 0]), y=float(traj_xy[0, 1]), heading_deg=float(heading_smooth[0]))
    prev_dist, prev_head = 0.0, float(heading_smooth[0])

    seed_rng = np.random.default_rng(42)
    pf = ParticleFilter(walls, n=600, rng=np.random.default_rng(123))
    pf.reset(x=float(traj_xy[0, 0]) + seed_rng.normal(0, 0.6),
              y=float(traj_xy[0, 1]) + seed_rng.normal(0, 0.6),
              theta_deg=float(heading_smooth[0]) + seed_rng.normal(0, 8.0),
              spread=0.6, theta_spread=15.0)

    mcl_records = []
    snapshots = {}
    snap_frames = sorted(set([2, n_frames // 3, min(58, n_frames - 1), n_frames - 1]))

    for i in range(n_frames):
        tx, ty, tth = float(traj_xy[i, 0]), float(traj_xy[i, 1]), float(heading_smooth[i])
        frng = np.random.default_rng(1000 + i)          # IDENTICAL seed formula -> identical observation to baseline
        obs = raycast_frame(tx, ty, tth, frng)

        pose = trk.update(imu_by_frame[i])
        dx = pose["distance_m"] - prev_dist
        dtheta = ((pose["heading_deg"] - prev_head + 180) % 360) - 180
        prev_dist, prev_head = pose["distance_m"], pose["heading_deg"]

        t0 = _time.time()
        pf.predict(dx=dx, dy=0.0, dtheta=dtheta,
                   noise=dict(fwd_m=0.03, fwd_frac=0.15, lat_m=0.05, theta_deg=5.0))
        pf.update(obs)
        elapsed_ms = (_time.time() - t0) * 1000.0
        est = pf.estimate()

        pos_err = float(np.hypot(est["x"] - tx, est["y"] - ty))
        th_err = float(abs(((est["theta_deg"] - tth + 180) % 360) - 180))
        rl_true = room_label(tx, ty)
        rl_est = room_label(est["x"], est["y"])
        mcl_records.append(dict(i=i, tx=tx, ty=ty, tth=tth, ex=est["x"], ey=est["y"],
                                 eth=est["theta_deg"], pos_err=pos_err, th_err=th_err,
                                 conf=est["confidence"], spread_m=est["spread_m"],
                                 n_eff=est["n_effective"], elapsed_ms=elapsed_ms,
                                 rl_true=rl_true, rl_est=rl_est))
        if i in snap_frames:
            snapshots[i] = dict(particles=pf.particles.copy(), weights=pf.weights.copy(),
                                 est=dict(est), tx=tx, ty=ty, tth=tth)

    # ---------------------------------------------------------- metrics
    def arr(recs, key):
        return np.array([r[key] if r[key] is not None else np.nan for r in recs], float)

    def summarize(recs, label):
        pe = arr(recs, "pos_err")
        th = arr(recs, "th_err")
        conf = arr(recs, "conf")
        el = arr(recs, "elapsed_ms")
        rl_true = np.array([r["rl_true"] for r in recs])
        rl_est = np.array([r["rl_est"] for r in recs])
        correct_room = float(np.mean(rl_true == rl_est)) * 100
        lost_at, rec_at = _lost_and_recovered(pe)
        valid = ~np.isnan(pe) & ~np.isnan(conf)
        corr = float(np.corrcoef(conf[valid], pe[valid])[0, 1]) if valid.sum() > 2 else float("nan")
        return dict(label=label, median=float(np.nanmedian(pe)), p90=float(np.nanpercentile(pe, 90)),
                    worst=float(np.nanmax(pe)), heading_median=float(np.nanmedian(th)),
                    correct_room_pct=correct_room, lost_at=lost_at, recovered_at=rec_at,
                    conf_err_corr=corr, median_ms=float(np.nanmedian(el)), n=len(recs))

    base_summary = summarize(baseline_records, "single-hypothesis (baseline)")
    mcl_summary = summarize(mcl_records, "particle filter (mcl.py)")

    print("\n" + "=" * 78)
    print(f"{'metric':<32}{'baseline':>20}{'MCL':>20}")
    print("=" * 78)
    for k, fmt, name in [
        ("median", "%.2f m", "median pos err"), ("p90", "%.2f m", "p90 pos err"),
        ("worst", "%.2f m", "worst pos err"), ("heading_median", "%.1f deg", "median heading err"),
        ("correct_room_pct", "%.1f%%", "correct-room rate"),
        ("conf_err_corr", "%.2f", "confidence-vs-error corr (r)"),
        ("median_ms", "%.1f ms", "median runtime/update"),
    ]:
        print(f"{name:<32}{fmt % base_summary[k]:>20}{fmt % mcl_summary[k]:>20}")
    print(f"{'lost lock at frame':<32}{str(base_summary['lost_at']):>20}{str(mcl_summary['lost_at']):>20}")
    print(f"{'recovered at frame':<32}{str(base_summary['recovered_at']):>20}{str(mcl_summary['recovered_at']):>20}")
    print("=" * 78)

    # ---------------------------------------------------------- plot
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    import shapely.geometry as geom

    tx = arr(mcl_records, "tx"); ty = arr(mcl_records, "ty")
    mex = arr(mcl_records, "ex"); mey = arr(mcl_records, "ey")
    mpe = arr(mcl_records, "pos_err")
    bex = arr(baseline_records, "ex"); bey = arr(baseline_records, "ey")
    bpe = arr(baseline_records, "pos_err")

    fig = plt.figure(figsize=(15, 15))
    gs = fig.add_gridspec(4, len(snap_frames), height_ratios=[3, 1, 1, 1.4])
    ax = fig.add_subplot(gs[0, :])

    segs = [[(w[0], w[1]), (w[2], w[3])] for w in walls]
    ax.add_collection(LineCollection(segs, colors="0.35", linewidths=1.0, zorder=1))
    for poly in pt.room_polys:
        xs, ys = poly.exterior.xy
        ax.fill(xs, ys, color="0.93", zorder=0, edgecolor="none")

    ax.plot(tx, ty, "-", color="tab:blue", lw=2.2, label="ground truth", zorder=3)
    ax.plot(tx[0], ty[0], "o", color="tab:blue", ms=10, zorder=4)
    ax.plot(bex, bey, "-", color="0.55", lw=1.3, alpha=0.85, label="baseline (single-hypothesis)", zorder=2)
    ax.plot(mex, mey, "-", color="tab:red", lw=1.8, label="MCL estimate", zorder=3)
    lost_mask = mpe > 1.5
    ax.plot(mex[lost_mask], mey[lost_mask], ".", color="orange", ms=6, zorder=5,
            label="MCL pos err > 1.5 m")
    for fnum in snap_frames:
        s = snapshots[fnum]
        ax.scatter(s["particles"][:, 0], s["particles"][:, 1], s=3, color="tab:red", alpha=0.15, zorder=2)
        ax.annotate(f"f{fnum}", (s["est"]["x"], s["est"]["y"]), color="darkred", fontsize=8,
                    xytext=(4, 4), textcoords="offset points")

    ax.set_aspect("equal")
    ax.set_title("MCL vs. single-hypothesis tracker on the same 116-frame level-2 walk\n"
                 "blue=ground truth, grey=baseline, red=MCL (+ particle cloud at snapshot frames)")
    ax.legend(loc="upper right", fontsize=9)
    ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)")
    pad = 3
    ax.set_xlim(walls[:, [0, 2]].min() - pad, walls[:, [0, 2]].max() + pad)
    ax.set_ylim(walls[:, [1, 3]].min() - pad, walls[:, [1, 3]].max() + pad)

    idx = np.arange(n_frames)
    ax2 = fig.add_subplot(gs[1, :])
    ax2.plot(idx, bpe, color="0.55", lw=1.2, label="baseline pos err")
    ax2.plot(idx, mpe, color="tab:red", lw=1.4, label="MCL pos err")
    ax2.axhline(1.5, color="orange", ls="--", lw=0.8)
    ax2.set_ylabel("pos err (m)"); ax2.legend(fontsize=8); ax2.set_xlim(0, n_frames - 1)

    ax3 = fig.add_subplot(gs[2, :])
    ax3.plot(idx, arr(mcl_records, "conf"), color="tab:green", lw=1.4, label="MCL confidence")
    ax3.set_ylabel("confidence"); ax3.set_xlabel("frame"); ax3.legend(fontsize=8)
    ax3.set_xlim(0, n_frames - 1); ax3.set_ylim(0, 1)

    # per-snapshot local particle-cloud zoom: ground truth (blue star), MCL
    # estimate (red dot), and the live particle cloud (dot size/alpha ~ weight)
    # in a window around the true pose, so the belief's shape is visible.
    for col, fnum in enumerate(snap_frames):
        axp = fig.add_subplot(gs[3, col])
        s = snapshots[fnum]
        pts, wts = s["particles"], s["weights"]
        wn = wts / wts.max() if wts.max() > 0 else wts
        axp.add_collection(LineCollection(segs, colors="0.6", linewidths=0.7, zorder=1))
        axp.scatter(pts[:, 0], pts[:, 1], s=6 + 40 * wn, c="tab:red", alpha=0.35, zorder=2,
                    edgecolors="none")
        axp.plot(s["tx"], s["ty"], "*", color="tab:blue", ms=14, zorder=4, label="truth")
        axp.plot(s["est"]["x"], s["est"]["y"], "o", color="darkred", ms=6, zorder=5, label="MCL est")
        win = 4.0
        axp.set_xlim(s["tx"] - win, s["tx"] + win)
        axp.set_ylim(s["ty"] - win, s["ty"] + win)
        axp.set_aspect("equal")
        axp.set_title(f"frame {fnum}  conf={s['est']['confidence']:.2f}  "
                       f"n_eff={s['est']['n_effective']:.0f}", fontsize=8)
        axp.set_xticks([]); axp.set_yticks([])
        if col == 0:
            axp.legend(fontsize=6, loc="upper left")
    plt.tight_layout()
    out_path = "/home/acer01/plans/mcl_proof.png"
    plt.savefig(out_path, dpi=140)
    print(f"[mcl] saved {out_path}")

    with open(os.path.join(here, "_mcl_records.pkl"), "wb") as f:
        pickle.dump(dict(mcl_records=mcl_records, baseline_records=baseline_records,
                          base_summary=base_summary, mcl_summary=mcl_summary), f)

    return base_summary, mcl_summary


if __name__ == "__main__":
    _run_benchmark()
