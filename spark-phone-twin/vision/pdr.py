"""Pedestrian dead reckoning (PDR): turns a stream of phone accelerometer /
gyro samples into an incremental 2D pose, independent of vision.

Why this exists: vision-only localization on a floor plan with a repeating
6.4 m structural bay is ambiguous by construction -- two corridors 6.4 m
apart can look identical to a single camera frame. A step taken by the
walker is not ambiguous in that way: it is a step regardless of which
corridor it happens to look like. PDR carries the pose through those
stretches; vision (via fuse(), see below) only gets to correct it when it
is both confident AND plausible.

Standalone and testable -- no import of phone_bridge or spark_app. Run
`python pdr.py` for a self-contained synthetic validation (see __main__).

    from pdr import PDRTracker, fuse
    trk = PDRTracker(step_length_m=0.7, method="fixed")
    trk.set_anchor(x=10.21, y=-22.6, heading_deg=180.0)   # operator's QR click
    pose = trk.update(samples)   # samples = list of dicts, see SAMPLE FORMAT

SAMPLE FORMAT (each dict; all fields optional except t):
    t          -- seconds, monotonically increasing (any epoch is fine as
                  long as it's consistent within a session)
    ax, ay, az -- linear acceleration m/s^2 (gravity already removed), i.e.
                  DeviceMotionEvent.acceleration
    agx,agy,agz-- acceleration INCLUDING gravity, i.e.
                  DeviceMotionEvent.accelerationIncludingGravity -- used to
                  derive a magnitude if ax/ay/az is absent (common on
                  Android where .acceleration is null without a gyro)
    rr_alpha   -- rotationRate.alpha, deg/s (yaw-ish, phone held upright)
    compass_deg-- best available heading (webkitCompassHeading, or
                  DeviceOrientationEvent alpha converted, or absolute
                  orientation heading) -- used ONLY as a slow correction,
                  see _update_heading()
"""
import math
import numpy as np

try:
    from scipy.signal import find_peaks
    HAVE_SCIPY = True
except Exception:
    HAVE_SCIPY = False

# ---- tunables -----------------------------------------------------------
REFRACTORY_S = 0.25          # min time between steps (~240 spm cap)
LOWPASS_ALPHA = 0.25         # EMA coefficient for the slow "gravity/baseline" track
STEP_LENGTH_DEFAULT_M = 0.70 # fixed step length fallback
WEINBERG_K = 0.50            # SL = K * (a_max - a_min) ** 0.25  (Weinberg 2002-style)
DRIFT_RATE = 0.075           # PDR error grows ~7.5% of distance travelled (mid of 5-10%)
COMPASS_GAIN = 0.03          # per-sample pull of heading toward compass reference (slow)
MIN_PROMINENCE_MS2 = 0.9     # floor so standing-still noise never registers as a step


def _wrap180(deg):
    return (deg + 180.0) % 360.0 - 180.0


def _wrap360(deg):
    return deg % 360.0


def lowpass_ema(x, alpha=LOWPASS_ALPHA):
    """Simple exponential-moving-average low-pass filter."""
    y = np.empty_like(x, dtype=np.float64)
    acc = x[0]
    for i, v in enumerate(x):
        acc = alpha * v + (1 - alpha) * acc
        y[i] = acc
    return y


def accel_magnitude(samples):
    """Per-sample |acceleration| in m/s^2, preferring gravity-removed
    (ax,ay,az) and falling back to accelerationIncludingGravity with its
    own slow-tracked gravity component subtracted."""
    have_lin = all(s.get("ax") is not None for s in samples)
    if have_lin:
        a = np.array([[s["ax"], s["ay"], s["az"]] for s in samples], np.float64)
        # Project onto gravity to get SIGNED vertical acceleration. The norm
        # would rectify the stride oscillation and double the step count.
        have_g = all(s.get("agz") is not None for s in samples)
        if have_g:
            g = np.array([[s.get("agx") or 0.0, s.get("agy") or 0.0,
                           s.get("agz") or 9.81] for s in samples], np.float64)
            gn = np.linalg.norm(g, axis=1, keepdims=True)
            gn[gn < 1e-6] = 1.0
            return np.sum(a * (g / gn), axis=1)
        # No gravity vector: fall back to the dominant axis, still signed.
        var = a.var(axis=0)
        return a[:, int(np.argmax(var))]
    # fallback: incl-gravity magnitude minus a slow-tracked baseline (~9.81)
    g = np.array([[s.get("agx", 0.0) or 0.0, s.get("agy", 0.0) or 0.0,
                   s.get("agz", 9.81) or 9.81] for s in samples], np.float64)
    mag = np.linalg.norm(g, axis=1)
    baseline = lowpass_ema(mag, alpha=0.05)   # gravity changes slowly vs. gait
    return mag - baseline


def detect_steps(t, mag, refractory_s=REFRACTORY_S):
    """Low-pass + adaptive-threshold peak detection on accel magnitude.

    Returns (peak_indices, peak_vals, trough_vals) -- trough is the local
    minimum immediately preceding each peak, used for step-length estimation.
    """
    t = np.asarray(t, np.float64)
    mag = np.asarray(mag, np.float64)
    if len(mag) < 5:
        return np.array([], int), np.array([]), np.array([])

    dt = np.median(np.diff(t)) if len(t) > 1 else 0.02
    dt = dt if dt > 1e-4 else 0.02
    min_dist = max(1, int(round(refractory_s / dt)))

    smooth = lowpass_ema(mag, alpha=0.35)          # denoise, keep gait shape
    dyn = mag - lowpass_ema(mag, alpha=0.06)        # remove slow drift/baseline
    std = float(np.std(dyn)) if len(dyn) > 1 else 0.0
    prominence = max(MIN_PROMINENCE_MS2, 0.55 * std)

    if HAVE_SCIPY:
        peaks, _ = find_peaks(smooth, distance=min_dist, prominence=prominence)
    else:
        # dependency-free fallback: local-max scan with the same refractory gate
        peaks = []
        last = -min_dist
        for i in range(1, len(smooth) - 1):
            if smooth[i] > smooth[i - 1] and smooth[i] >= smooth[i + 1] \
                    and (smooth[i] - min(smooth[max(0, i - min_dist):i + 1])) >= prominence:
                if i - last >= min_dist:
                    peaks.append(i)
                    last = i
        peaks = np.array(peaks, int)

    troughs = []
    for p in peaks:
        lo = max(0, p - min_dist)
        troughs.append(float(np.min(smooth[lo:p + 1])) if p > lo else smooth[p])
    return peaks, smooth[peaks] if len(peaks) else np.array([]), np.array(troughs)


def step_length(peak_val, trough_val, method="fixed", fixed_m=STEP_LENGTH_DEFAULT_M,
                 k=WEINBERG_K):
    if method == "weinberg":
        spread = max(0.0, peak_val - trough_val)
        return float(k * (spread ** 0.25)) if spread > 0 else fixed_m
    return float(fixed_m)


class PDRTracker:
    """Incremental PDR state machine. Feed it batches of raw samples (as
    posted by the phone at whatever cadence the client flushes); it
    maintains steps, heading and (x, y) between absolute fixes."""

    def __init__(self, step_length_m=STEP_LENGTH_DEFAULT_M, method="fixed",
                 drift_rate=DRIFT_RATE, compass_gain=COMPASS_GAIN,
                 refractory_s=REFRACTORY_S, history_s=4.0):
        self.step_length_m = step_length_m
        self.method = method            # "fixed" or "weinberg"
        self.drift_rate = drift_rate
        self.compass_gain = compass_gain
        self.refractory_s = refractory_s
        self.history_s = history_s      # rolling context kept for peak detection

        self.x = 0.0
        self.y = 0.0
        self.heading_deg = 0.0
        self.steps = 0                  # lifetime step count
        self.distance_m = 0.0           # distance since last anchor/reset
        self._buf = []                  # rolling raw-sample window
        self._last_step_t = None
        self._last_gyro_t = None
        self._compass_offset = None     # plan_heading - compass_deg, set at anchor
        self._have_anchor = False

    def set_anchor(self, x, y, heading_deg, compass_deg=None, t=None):
        """Absolute fix (operator's QR click, or an accepted vision fix via
        fuse()). Resets position AND zeroes the drift estimate. If a compass
        reading is supplied at the same instant, it calibrates the offset
        between compass frame and plan frame so the compass can keep being
        useful as a *slow* correction afterwards (see _update_heading)."""
        self.x, self.y = float(x), float(y)
        self.heading_deg = _wrap360(heading_deg)
        self.distance_m = 0.0
        self._have_anchor = True
        if compass_deg is not None:
            self._compass_offset = _wrap180(self.heading_deg - compass_deg)
        self._last_gyro_t = t
        self._buf.clear()
        self._last_step_t = None

    @property
    def drift_estimate_m(self):
        """Honest, monotonically-growing uncertainty since the last absolute
        fix. PDR alone accumulates roughly 5-10% of distance travelled in
        error (heading error compounds via dead reckoning); we report the
        midpoint by default. Callers should distrust PDR more as this grows,
        and treat a fresh anchor/accepted vision fix as resetting it to 0."""
        return round(self.distance_m * self.drift_rate, 3)

    def _update_heading(self, samples):
        """Integrate gyro (rotationRate.alpha) for heading; compass, when
        present, only ever pulls the estimate slowly toward its own
        plan-frame equivalent (compass_deg + calibrated offset) -- never
        overwrites it outright. This is the point of the spec: gyro *change*
        is trustworthy over short spans, absolute compass is not, indoors,
        near steel."""
        for s in samples:
            t = s.get("t")
            rr = s.get("rr_alpha")
            if t is not None and rr is not None and self._last_gyro_t is not None:
                dt = t - self._last_gyro_t
                if 0 < dt < 1.0:      # ignore bogus/huge gaps (e.g. after a pause)
                    self.heading_deg = _wrap360(self.heading_deg + rr * dt)
            if t is not None:
                self._last_gyro_t = t

            comp = s.get("compass_deg")
            if comp is not None:
                if self._compass_offset is None:
                    # first compass sample after an anchor with no compass at
                    # anchor time -- calibrate opportunistically
                    self._compass_offset = _wrap180(self.heading_deg - comp)
                ref = _wrap360(comp + self._compass_offset)
                err = _wrap180(ref - self.heading_deg)
                self.heading_deg = _wrap360(self.heading_deg + self.compass_gain * err)

    def update(self, samples):
        """samples: list of new raw sample dicts since the last call (may be
        empty). Returns the tracker's current cumulative pose."""
        if samples:
            samples = sorted(samples, key=lambda s: s.get("t", 0.0))
            self._update_heading(samples)
            self._buf.extend(samples)

        # keep only a rolling window of raw samples for peak-detection context
        if self._buf:
            t_now = self._buf[-1].get("t", 0.0)
            self._buf = [s for s in self._buf if t_now - s.get("t", t_now) <= self.history_s]

        if len(self._buf) >= 5:
            ts = [s.get("t", i * 0.02) for i, s in enumerate(self._buf)]
            mag = accel_magnitude(self._buf)
            peaks, pvals, tvals = detect_steps(ts, mag, self.refractory_s)
            for idx, pv, tv in zip(peaks, pvals, tvals):
                pt = ts[idx]
                if self._last_step_t is not None and pt <= self._last_step_t:
                    continue  # already counted in a previous update() call
                self._last_step_t = pt
                sl = step_length(pv, tv, self.method, self.step_length_m)
                # Compass convention: 0 deg = north = +y, 90 deg = east = +x.
                # Using cos for x (the maths convention) walks 90 degrees off.
                hd = math.radians(self.heading_deg)
                self.x += sl * math.sin(hd)
                self.y += sl * math.cos(hd)
                self.distance_m += sl
                self.steps += 1

        return {
            "x": round(self.x, 3), "y": round(self.y, 3),
            "heading_deg": round(self.heading_deg, 1),
            "steps": self.steps,
            "distance_m": round(self.distance_m, 3),
            "drift_estimate_m": self.drift_estimate_m,
        }


# --------------------------------------------------------------------- #
# TASK C -- fusion policy
# --------------------------------------------------------------------- #
def fuse(vision_pose, pdr_pose, vision_conf, dt,
         conf_threshold=0.55, base_agreement_m=1.5, drift_margin_factor=1.0):
    """Decide what to believe this cycle.

    Policy: PDR carries the pose between fixes. Vision is allowed to correct
    it only when BOTH:
      (a) vision_conf >= conf_threshold, and
      (b) vision's (x, y) is within a plausible radius of PDR's (x, y).
    The plausible radius grows with PDR's own drift estimate, so a phone
    that has walked 20 m since its last fix is allowed a bigger correction
    than one that just started -- but a confident vision fix that disagrees
    by more than that is REJECTED outright, because that disagreement is the
    signature of corridor-aliasing (two 6.4 m-apart corridors mis-scored as
    equally likely), not real motion. A rejected fix does not perturb the
    PDR track at all.

    When accepted, position is blended (weight = vision_conf, capped at 0.85
    so a single frame can never fully overwrite the dead-reckoned track) and
    the drift estimate is NOT necessarily zeroed -- only set_anchor() (an
    operator's QR click, or a caller-chosen "trust this fix completely" path)
    zeroes drift. A blended correction reduces trust in the old PDR estimate
    but a partial vision frame is not the same guarantee as an anchor.

    Args:
        vision_pose: {"x":, "y":, "heading_deg":} or None if no vision this cycle
        pdr_pose:    {"x":, "y":, "heading_deg":, "drift_estimate_m":}
        vision_conf: float in [0, 1] (or None)
        dt:          seconds since the previous fuse() call (kept for API
                     completeness / future velocity-consistency checks)

    Returns dict: {x, y, heading_deg, source, accepted, reason, disagreement_m}
    """
    px, py, ph = pdr_pose["x"], pdr_pose["y"], pdr_pose.get("heading_deg", 0.0)
    drift = pdr_pose.get("drift_estimate_m", 0.0) or 0.0

    if not vision_pose or vision_conf is None:
        return dict(x=px, y=py, heading_deg=ph, source="pdr", accepted=False,
                    reason="no vision this cycle", disagreement_m=None)

    vx, vy = vision_pose["x"], vision_pose["y"]
    vh = vision_pose.get("heading_deg")
    disagreement = math.hypot(vx - px, vy - py)
    plausible_radius = base_agreement_m + drift_margin_factor * drift

    if vision_conf < conf_threshold:
        return dict(x=px, y=py, heading_deg=ph, source="pdr", accepted=False,
                    reason="vision confidence %.2f below threshold %.2f" %
                           (vision_conf, conf_threshold),
                    disagreement_m=round(disagreement, 2))

    if disagreement > plausible_radius:
        return dict(x=px, y=py, heading_deg=ph, source="pdr", accepted=False,
                    reason="rejected: vision %.2fm from PDR exceeds plausible "
                           "radius %.2fm (suspected corridor-aliasing)" %
                           (disagreement, plausible_radius),
                    disagreement_m=round(disagreement, 2))

    w = min(0.85, vision_conf)
    fx = w * vx + (1 - w) * px
    fy = w * vy + (1 - w) * py
    fh = ph
    if vh is not None:
        fh = _wrap360(ph + w * _wrap180(vh - ph))
    return dict(x=round(fx, 3), y=round(fy, 3), heading_deg=round(fh, 1),
                source="blend", accepted=True,
                reason="vision agrees within %.2fm (conf %.2f) -> blended w=%.2f" %
                       (plausible_radius, vision_conf, w),
                disagreement_m=round(disagreement, 2))


# --------------------------------------------------------------------- #
# Synthetic validation -- no phone required.
# --------------------------------------------------------------------- #
def _synthesize_walk(n_steps_leg1=15, n_steps_leg2=15, step_len=0.7, hz=50,
                      cadence_hz=1.8, turn_deg=90.0, seed=0):
    """Fake accelerometer + gyro trace: walk `leg1` steps heading 0deg, turn
    `turn_deg` over ~1s, walk `leg2` steps heading `turn_deg`. Returns
    (samples, ground_truth) where ground_truth has the true final (x, y) and
    total step count, computed from closed-form geometry (not the sim loop),
    so it's independent of the synthesis method."""
    rng = np.random.default_rng(seed)
    dt = 1.0 / hz
    step_period = 1.0 / cadence_hz
    total_steps = n_steps_leg1 + n_steps_leg2
    walk_duration = total_steps * step_period
    turn_start = n_steps_leg1 * step_period
    turn_duration = 1.0
    total_duration = walk_duration + turn_duration

    t = 0.0
    samples = []
    while t < total_duration:
        # gait bump: real |linear-acceleration| from a walking phone is a
        # one-sided impact pulse once per step (heel strike / push-off), not
        # a signal that swings symmetrically negative -- so model it as a
        # positive Gaussian bump. (A symmetric sine here would fold its
        # negative half back into a second positive peak once you take the
        # vector norm, silently doubling the detected step count.)
        phase = (t % step_period) / step_period
        amp = 4.0 if (t < turn_start or t > turn_start + turn_duration) else 1.6
        gait = amp * math.exp(-0.5 * ((phase - 0.3) / 0.12) ** 2)
        noise = rng.normal(0, 0.25)
        mag = gait + noise
        # split magnitude arbitrarily across axes (detector uses magnitude only)
        ax, ay, az = mag / math.sqrt(3), mag / math.sqrt(3), mag / math.sqrt(3)

        if turn_start <= t < turn_start + turn_duration:
            rr_alpha = turn_deg / turn_duration + rng.normal(0, 2.0)
        else:
            rr_alpha = rng.normal(0, 1.0)   # gyro noise while walking straight

        samples.append(dict(t=round(t, 4), ax=ax, ay=ay, az=az, rr_alpha=rr_alpha))
        t += dt

    gt_x1 = n_steps_leg1 * step_len
    gt_y1 = 0.0
    hd2 = math.radians(turn_deg)
    gt_x = gt_x1 + n_steps_leg2 * step_len * math.cos(hd2)
    gt_y = gt_y1 + n_steps_leg2 * step_len * math.sin(hd2)
    gt = dict(x=gt_x, y=gt_y, steps=total_steps,
              distance_m=total_steps * step_len, heading_deg=turn_deg)
    return samples, gt


def _run_validation():
    print("=" * 70)
    print("1) Synthetic 30-step walk (15 straight, 90deg turn, 15 more), 0.7m/step")
    print("=" * 70)
    samples, gt = _synthesize_walk()
    trk = PDRTracker(step_length_m=0.7, method="fixed")
    trk.set_anchor(x=0.0, y=0.0, heading_deg=0.0)

    # feed in batches of ~10 samples, mimicking a phone flushing periodically
    batch = 10
    pose = None
    for i in range(0, len(samples), batch):
        pose = trk.update(samples[i:i + batch])
    pose = trk.update([])

    err = math.hypot(pose["x"] - gt["x"], pose["y"] - gt["y"])
    print("ground truth : x=%.2f y=%.2f  steps=%d  distance=%.2fm" %
          (gt["x"], gt["y"], gt["steps"], gt["distance_m"]))
    print("PDR estimate : x=%.2f y=%.2f  steps=%d  distance=%.2fm  heading=%.1fdeg" %
          (pose["x"], pose["y"], pose["steps"], pose["distance_m"], pose["heading_deg"]))
    print("position error: %.3f m   |   steps detected vs actual: %d / %d" %
          (err, pose["steps"], gt["steps"]))
    print("drift_estimate_m at end: %.3f (%.1f%% of distance travelled)" %
          (pose["drift_estimate_m"], 100 * pose["drift_estimate_m"] / max(pose["distance_m"], 1e-6)))

    print()
    print("=" * 70)
    print("2) Drift estimate growth vs. distance")
    print("=" * 70)
    trk2 = PDRTracker(step_length_m=0.7)
    trk2.set_anchor(0, 0, 0)
    checkpoints = []
    for i in range(0, len(samples), batch):
        p = trk2.update(samples[i:i + batch])
        checkpoints.append((p["distance_m"], p["drift_estimate_m"]))
    for d, dr in checkpoints[::max(1, len(checkpoints)//8)]:
        print("  distance=%6.2fm  ->  drift_estimate=%6.3fm  (%.1f%%)" %
              (d, dr, 100 * dr / max(d, 1e-6)))
    monotonic = all(checkpoints[i][1] <= checkpoints[i + 1][1] + 1e-9
                     for i in range(len(checkpoints) - 1))
    print("drift is monotonically non-decreasing with distance:", monotonic)

    print()
    print("=" * 70)
    print("3) Fusion policy vs. simulated corridor-aliasing")
    print("=" * 70)
    pdr_pose = dict(x=10.0, y=5.0, heading_deg=0.0, drift_estimate_m=0.5)
    # (a) confident vision fix 8m away -> must be rejected
    vision_far = dict(x=18.0, y=5.0, heading_deg=0.0)
    r1 = fuse(vision_far, pdr_pose, vision_conf=0.9, dt=0.4)
    print("confident vision 8.0m away  -> accepted=%s source=%s (%s)" %
          (r1["accepted"], r1["source"], r1["reason"]))
    assert r1["accepted"] is False and r1["source"] == "pdr", "aliasing rejection FAILED"

    # (b) confident vision close by -> should be accepted / blended
    vision_close = dict(x=10.6, y=5.3, heading_deg=5.0)
    r2 = fuse(vision_close, pdr_pose, vision_conf=0.9, dt=0.4)
    print("confident vision 0.67m away -> accepted=%s source=%s (%s)" %
          (r2["accepted"], r2["source"], r2["reason"]))
    assert r2["accepted"] is True and r2["source"] == "blend", "agreement acceptance FAILED"

    # (c) low-confidence vision, even if close -> rejected on confidence alone
    r3 = fuse(vision_close, pdr_pose, vision_conf=0.2, dt=0.4)
    print("low-confidence vision       -> accepted=%s source=%s (%s)" %
          (r3["accepted"], r3["source"], r3["reason"]))
    assert r3["accepted"] is False

    print()
    print("All assertions passed.")


if __name__ == "__main__":
    _run_validation()
