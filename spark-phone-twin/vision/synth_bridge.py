"""A fake phone that walks a known path, so pose tracking can be tested for real.

Testing pdr.py alone passed while the app stayed frozen, because the fault was
never in the step detector -- it was in the integration around it (PDR tied to
camera frames, a stale motion buffer, teleporting writers). So this impersonates
the bridge instead of the algorithm: the REAL spark_app polls these endpoints
with BRIDGE_URL pointed here and has no idea the phone is not a phone.

The walk is driven by wall-clock time, not by request count, so the answer does
not depend on how often the app happens to poll.

Ground truth path (metres, building frame), starting at the anchor:
    leg 1: 12 m east   at heading  90
    leg 2:  6 m north  at heading   0
Sampling is 60 Hz accelerometer with a 1.9 Hz gait, matching a real handset.
"""
import json, math, os, sys, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("SYNTH_PORT", "8199"))
DEV = os.environ.get("SYNTH_DEV", "synthwalk")

# --- ground truth ---------------------------------------------------------
# (duration_s, heading_deg, speed_mps). 1.2 m/s is an ordinary indoor walk.
LEGS = [
    (2.00, 0.0, 0.00),
    (3.23, 180.0, 1.30),
    (1.08, 270.0, 1.30),
    (4.92, 0.0, 1.30),
    (18.00, 90.0, 1.30),
    (2.00, 90.0, 0.00),
]
T0 = None          # set on the first /motion request: the app takes ~14 s to
                   # start, and the walk should begin when it is actually looking
RATE = 60.0
GAIT_HZ = 1.9


def _clock():
    global T0
    if T0 is None:
        T0 = time.time()
        print("[synth] walk started (first motion request)", flush=True)
    return T0


def truth_at(t):
    """Ground-truth (x, y, heading, moving) at t seconds after start."""
    x = y = 0.0
    hd = LEGS[0][1]
    rem = t
    for dur, heading, spd in LEGS:
        hd = heading
        step = min(rem, dur)
        if step > 0:
            r = math.radians(heading)
            # heading 0 = +y (north), 90 = +x (east): the app's map convention
            x += spd * step * math.sin(r)
            y += spd * step * math.cos(r)
        rem -= step
        if rem <= 0:
            return x, y, hd, spd > 0
    return x, y, hd, False


def total_truth():
    return truth_at(sum(l[0] for l in LEGS))


def motion_window(now, n=600):
    """The last n accelerometer samples at 60 Hz, ending at `now`.

    Samples carry gait swing only while the ground truth says we are moving, so
    a detector that invents steps while standing still fails the test.
    """
    out = []
    for i in range(n):
        ts = now - (n - 1 - i) / RATE
        rel = ts - _clock()
        if rel < 0:
            rel = 0.0
        _x, _y, hd, moving = truth_at(rel)
        if moving:
            # vertical gait swing; amplitude typical of a hand-held phone
            sw = 2.6 * math.sin(2 * math.pi * GAIT_HZ * rel)
            lat = 0.5 * math.sin(2 * math.pi * GAIT_HZ * rel + 1.1)
        else:
            sw = 0.02 * math.sin(2 * math.pi * 0.3 * rel)   # sensor noise only
            lat = 0.01
        out.append(dict(ax=round(lat, 4), ay=0.0, az=round(sw, 4),
                        agx=round(lat, 4), agy=0.0, agz=round(9.81 + sw, 4),
                        interval=1000.0 / RATE,
                        rr_alpha=0.0, rr_beta=0.0, rr_gamma=0.0,
                        t=round(ts, 4)))
    return out


def sensor_now(now):
    _x, _y, hd, moving = truth_at(now - _clock())
    return dict(heading=round(hd, 1), gx=0.0, gy=0.0, gz=-9.81,
                rr_alpha=0.0, rr_beta=0.0, rr_gamma=0.0,
                lat=47.6280, lon=-122.3400, acc=8.0,
                speed=1.2 if moving else 0.0)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        now = time.time()
        p = self.path.split("?")[0]
        if p == "/devices":
            return self._j({"devices": [dict(
                id=DEV, name="synthetic phone", age=0.1, frames=999,
                live=True, sensor=sensor_now(now))], "url": ""})
        if p.startswith("/motion/"):
            _clock()
            m = motion_window(now)
            return self._j({"device": DEV, "motion": m, "orientation": [],
                            "motion_buffered": len(m), "orientation_buffered": 0,
                            "motion_sample": m[-1], "orientation_sample": None})
        if p.startswith("/sensor/"):
            return self._j(sensor_now(now))
        if p.startswith("/latest/"):
            # no camera: PDR must work without it, which is the whole point
            self.send_response(404)
            self.end_headers()
            return
        return self._j({"ok": True})

    def do_POST(self):
        return self._j({"ok": True})


if __name__ == "__main__":
    gx, gy, _h, _m = total_truth()
    print("[synth] serving on %d as %s" % (PORT, DEV), flush=True)
    print("[synth] ground truth displacement: dx=%.2f dy=%.2f (|d|=%.2f m)"
          % (gx, gy, math.hypot(gx, gy)), flush=True)
    print("[synth] duration: %.0fs" % sum(l[0] for l in LEGS), flush=True)
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
