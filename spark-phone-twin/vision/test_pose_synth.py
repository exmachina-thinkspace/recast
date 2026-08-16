"""End-to-end pose test: does the marker on the 3D map follow a known walk?

Runs the REAL spark_app against synth_bridge.py, which impersonates a phone
walking a scripted path. Testing the estimator in isolation is what let the
earlier failures hide -- pdr.py passed its own validation while the app's marker
never moved, because the breakage was in the wiring, not the maths.

Checks, in order of what actually went wrong before:
  1. it moves at all
  2. it moves the right DISTANCE (step length / scale)
  3. it moves in the right DIRECTION (heading sign and frame convention)
  4. it does not teleport (continuity)
  5. it does not drift while standing still (false steps)
"""
import json, math, os, subprocess, sys, time

HOME = os.path.expanduser("~")
APP = os.path.join(HOME, "arlo-vision")
TRACE = os.path.join(HOME, "plans", "pose_trace.jsonl")
LOG = os.path.join(APP, "app.log")
SYNTH_PORT = 8199
WALK_S = 21.0            # total scripted duration
SETTLE_S = 16.0          # app start-up; the walk begins when it first polls

# ground truth, mirrored from synth_bridge.LEGS
TRUTH_DX, TRUTH_DY = 12.0, 6.0
TRUTH_D = math.hypot(TRUTH_DX, TRUTH_DY)

# PDR is dead reckoning: 10-25% distance error is normal and acceptable.
# The test exists to catch "does not move" and "moves wrongly", not to demand
# survey accuracy from an accelerometer.
DIST_TOL = 0.45          # fraction of ground-truth distance
BEARING_TOL_DEG = 40.0
MAX_SPEED = 2.0
STILL_DRIFT_M = 1.5


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def start_all():
    sh("pkill -f 'spark_app.py'")
    sh("pkill -f 'synth_bridge.py'")
    time.sleep(3)
    open(TRACE, "w").close()                     # fresh trace per run
    subprocess.Popen(
        ["%s/bin/python" % APP, "%s/synth_bridge.py" % APP],
        stdout=open("/tmp/synth.log", "w"), stderr=subprocess.STDOUT,
        cwd=APP, start_new_session=True)
    time.sleep(2)
    env = dict(os.environ,
               BRIDGE_URL="http://127.0.0.1:%d" % SYNTH_PORT,
               DISPLAY=":1", SHOT_EVERY="2.0",
               XAUTHORITY="/run/user/1000/gdm/Xauthority")
    subprocess.Popen(["%s/bin/python" % APP, "%s/spark_app.py" % APP],
                     stdout=open(LOG, "w"), stderr=subprocess.STDOUT,
                     cwd=APP, env=env, start_new_session=True)


def stop_all():
    sh("pkill -f 'spark_app.py'")
    sh("pkill -f 'synth_bridge.py'")


def read_track():
    """Positions over time for the synthetic device, from the app's own trace."""
    rows = []
    if not os.path.exists(TRACE):
        return rows
    for ln in open(TRACE):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("x") is None or r.get("y") is None:
            continue
        rows.append(r)
    return rows


def read_pdr_lines():
    if not os.path.exists(LOG):
        return []
    out = []
    for ln in open(LOG, errors="ignore"):
        if "[pdr]" in ln and "walked" in ln:
            out.append(ln.strip())
    return out


def evaluate():
    rows = read_track()
    pdr = read_pdr_lines()
    fails, notes = [], []
    notes.append("trace rows: %d   [pdr] walked lines: %d" % (len(rows), len(pdr)))

    if len(rows) < 5:
        fails.append("almost no pose samples (%d) - the app never tracked the "
                     "synthetic phone" % len(rows))
        return fails, notes

    xs = [r["x"] for r in rows]
    ys = [r["y"] for r in rows]
    x0, y0 = xs[0], ys[0]
    dx, dy = xs[-1] - x0, ys[-1] - y0
    dist = math.hypot(dx, dy)
    notes.append("start=(%.2f, %.2f)  end=(%.2f, %.2f)" % (x0, y0, xs[-1], ys[-1]))
    notes.append("displacement dx=%.2f dy=%.2f |d|=%.2f m  (truth dx=%.1f dy=%.1f "
                 "|d|=%.2f m)" % (dx, dy, dist, TRUTH_DX, TRUTH_DY, TRUTH_D))

    # 1. moved at all
    if dist < 1.0:
        fails.append("position did not move (|d|=%.2f m)" % dist)

    # 2. right distance
    if abs(dist - TRUTH_D) > DIST_TOL * TRUTH_D:
        fails.append("distance off by %.0f%% (got %.2f m, expected %.2f +-%.0f%%)"
                     % (100 * abs(dist - TRUTH_D) / TRUTH_D, dist, TRUTH_D,
                        100 * DIST_TOL))

    # 3. right direction
    if dist >= 1.0:
        got = math.degrees(math.atan2(dx, dy)) % 360.0
        want = math.degrees(math.atan2(TRUTH_DX, TRUTH_DY)) % 360.0
        err = abs(((got - want + 180) % 360) - 180)
        notes.append("bearing got %.0f deg, truth %.0f deg, error %.0f deg"
                     % (got, want, err))
        if err > BEARING_TOL_DEG:
            fails.append("bearing off by %.0f deg (>%.0f) - heading sign or "
                         "frame convention is wrong" % (err, BEARING_TOL_DEG))

    # 4. no teleports
    worst = 0.0
    for a, b in zip(rows, rows[1:]):
        dt = max(1e-3, b["t"] - a["t"])
        d = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        sp = d / dt
        worst = max(worst, sp)
    notes.append("peak implied speed %.2f m/s (limit %.1f)" % (worst, MAX_SPEED))
    if worst > MAX_SPEED * 1.6:
        fails.append("teleport: %.1f m/s implied, a person cannot do that" % worst)

    # 5. no drift while standing still (the walk starts ~2 s in and ends 2 s early)
    t0 = rows[0]["t"]
    head = [r for r in rows if r["t"] - t0 < 2.0]
    tail = [r for r in rows if r["t"] - t0 > (WALK_S - 2.0)]
    for name, seg in (("start", head), ("end", tail)):
        if len(seg) >= 3:
            sd = max(math.hypot(p["x"] - seg[0]["x"], p["y"] - seg[0]["y"])
                     for p in seg)
            notes.append("stationary drift at %s: %.2f m" % (name, sd))
            if sd > STILL_DRIFT_M:
                fails.append("drifted %.2f m while standing still at %s"
                             % (sd, name))
    return fails, notes


def main():
    print("=" * 64)
    print("SYNTHETIC POSE TEST - real app, fake phone walking a known path")
    print("=" * 64)
    start_all()
    total = SETTLE_S + WALK_S + 4
    print("running for %.0fs ..." % total)
    time.sleep(total)
    fails, notes = evaluate()
    stop_all()
    for n in notes:
        print("  " + n)
    if fails:
        print("\nFAIL:")
        for f in fails:
            print("  x " + f)
        return 1
    print("\nPASS: the marker followed the walk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
