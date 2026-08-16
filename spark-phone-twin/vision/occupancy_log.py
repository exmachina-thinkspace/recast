#!/usr/bin/env python3
"""
occupancy_log.py -- gives the digital twin a memory of time.

THE GAP: scenegraph.json (written by the live app / phone_bridge.py) carries
a `people_now` count per room, but every poll overwrites the last one. There
is no history -- the twin cannot say whether a room is busy "usually" or just
this instant, so build_vitals.py's utilization input is forced to lean on a
single --observe snapshot instead of real usage-over-time.

THE FIX: poll scenegraph.json on an interval and append one JSON record per
room per poll to an append-only log (~/plans/occupancy.jsonl). Never edit or
truncate existing lines -- this is a time series, not a cache. Downstream
consumers (vitals_occupancy.py, ask()) read the log and derive occupied
minutes, peak counts, and utilisation fractions.

This has NO dependency on VSS or any GPU model -- it only reads a JSON file
the running app already writes, so it works today.

Usage:
    python3 occupancy_log.py --poll --interval 15 --duration 90   # sample
    python3 occupancy_log.py --once                                # single sample
    python3 occupancy_log.py --summarise 24                        # report
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict

PLANS = os.path.expanduser("~/plans")
SCENEGRAPH = os.path.join(PLANS, "scenegraph.json")
LOG = os.path.join(PLANS, "occupancy.jsonl")


def _read_scenegraph_rooms():
    """Return [(level, room_id, people_now), ...] from the live twin's own
    scenegraph.json. Returns [] (not an exception) if the file is missing or
    mid-write -- a poller should skip a bad sample, not crash."""
    try:
        with open(SCENEGRAPH) as f:
            sg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    out = []
    for lvl in sg.get("levels", []):
        level = lvl.get("level")
        for room in lvl.get("rooms", []):
            out.append((level, room.get("room_id"), int(room.get("people_now", 0))))
    return out


def poll_once(interval_s: float, source: str = "scenegraph") -> int:
    """Append one occupancy record per room for the *current* scenegraph
    snapshot. `interval_s` is stored on every record so summarise() can turn
    a sample count into occupied-minutes without having to re-derive the
    polling cadence later. Returns the number of rooms recorded (0 if the
    scenegraph wasn't readable this cycle)."""
    rooms = _read_scenegraph_rooms()
    if not rooms:
        return 0
    ts = time.time()
    with open(LOG, "a") as f:
        for level, room_id, count in rooms:
            rec = {
                "ts": ts,
                "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
                "level": level,
                "room_id": room_id,
                "count": count,
                "interval_s": interval_s,
                "source": source,
            }
            f.write(json.dumps(rec) + "\n")
    return len(rooms)


def run(interval_s: float = 60.0, duration_s: float | None = None,
        source: str = "scenegraph"):
    """Poll forever (or for duration_s) at interval_s cadence. Meant to be
    run as a lightweight background loop -- it does no CV/model work, just
    reads a JSON file the app already maintains."""
    t_end = time.time() + duration_s if duration_s else None
    n_polls = 0
    while True:
        n = poll_once(interval_s, source=source)
        n_polls += 1
        print("[occupancy_log] poll %d: %d room records @ %s"
              % (n_polls, n, time.strftime("%H:%M:%S")), flush=True)
        if t_end and time.time() >= t_end:
            break
        time.sleep(interval_s)
    return n_polls


def _iter_log(since_ts: float):
    if not os.path.exists(LOG):
        return
    with open(LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ts", 0) >= since_ts:
                yield rec


def summarise(hours: float) -> dict:
    """Per-room occupied-minutes, peak count, and utilisation fraction over
    the trailing `hours`. utilisation = occupied_minutes / minutes actually
    covered by the log in that window (NOT hours*60 -- if the log only has
    5 minutes of data in a requested 24h window, we report that honestly via
    `coverage_minutes` rather than silently diluting the fraction).
    """
    now = time.time()
    since = now - hours * 3600.0
    per_room = defaultdict(lambda: {"level": None, "samples": 0, "occupied_seconds": 0.0,
                                      "peak": 0, "total_seconds": 0.0,
                                      "first_ts": None, "last_ts": None})
    for rec in _iter_log(since):
        rid = rec["room_id"]
        r = per_room[rid]
        r["level"] = rec.get("level")
        r["samples"] += 1
        dt = float(rec.get("interval_s", 0.0))
        r["total_seconds"] += dt
        if rec.get("count", 0) > 0:
            r["occupied_seconds"] += dt
        r["peak"] = max(r["peak"], int(rec.get("count", 0)))
        ts = rec["ts"]
        r["first_ts"] = ts if r["first_ts"] is None else min(r["first_ts"], ts)
        r["last_ts"] = ts if r["last_ts"] is None else max(r["last_ts"], ts)

    rooms_out = {}
    all_first, all_last, total_samples = None, None, 0
    for rid, r in per_room.items():
        cov_s = r["total_seconds"]
        util = (r["occupied_seconds"] / cov_s) if cov_s > 0 else 0.0
        rooms_out[rid] = {
            "level": r["level"],
            "samples": r["samples"],
            "occupied_minutes": round(r["occupied_seconds"] / 60.0, 2),
            "coverage_minutes": round(cov_s / 60.0, 2),
            "peak_count": r["peak"],
            "utilisation_fraction": round(util, 4),
        }
        total_samples += r["samples"]
        if r["first_ts"] is not None:
            all_first = r["first_ts"] if all_first is None else min(all_first, r["first_ts"])
            all_last = r["last_ts"] if all_last is None else max(all_last, r["last_ts"])

    hours_covered = ((all_last - all_first) / 3600.0) if (all_first and all_last and all_last > all_first) else 0.0
    return {
        "requested_hours": hours,
        "hours_covered": round(hours_covered, 4),
        "total_samples": total_samples,
        "rooms": rooms_out,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--poll", action="store_true", help="run the polling loop")
    ap.add_argument("--once", action="store_true", help="single poll then exit")
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--duration", type=float, default=None, help="stop polling after N seconds")
    ap.add_argument("--summarise", type=float, default=None, metavar="HOURS")
    a = ap.parse_args()

    if a.once:
        n = poll_once(a.interval)
        print("recorded %d room(s) to %s" % (n, LOG))
    elif a.poll:
        run(a.interval, a.duration)
    elif a.summarise is not None:
        print(json.dumps(summarise(a.summarise), indent=2))
    else:
        ap.print_help()
