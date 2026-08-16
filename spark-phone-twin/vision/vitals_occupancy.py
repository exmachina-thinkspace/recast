#!/usr/bin/env python3
"""
vitals_occupancy.py -- turns occupancy_log.py's time series into a
build_vitals.py-shaped input row: (value, weight, tier, source, note).

Deliberately a SEPARATE module. build_vitals.py is not edited; it could call
`occupancy_row()` from here to replace/augment the "camera occupancy" line in
its `use_utilization` vital, but the wiring decision is left to its owner.

EVIDENCE-TIER LADDER (extends build_vitals.py's own T0-T3 convention):
  T0  0 hours of logged coverage           -- no data, an honest gap
  T3  0 < hours_covered < T3_MIN_HOURS      -- real camera data, but too
      short a window to say anything about "utilization" as a pattern
      (a few minutes is a spot-check, not a usage rate) -- proxy-grade
  T1  hours_covered >= T1_MIN_HOURS         -- enough continuous coverage
      (defaults to 8h, i.e. a working day) to trust as direct observed
      utilization, same tier build_vitals.py already gives camera occupancy

Note there is no T2 step here: T2 in build_vitals.py's convention means an
"official record" (permits, assessor filings) -- camera-derived occupancy
is never that, no matter how much of it accumulates, so the ladder only
ever promotes T0 -> T3 -> T1.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import occupancy_log  # noqa: E402

T3_MIN_HOURS = 0.0   # any real sample at all clears T0
T1_MIN_HOURS = 8.0   # one working day of continuous coverage


def evidence_tier(hours_covered: float) -> str:
    if hours_covered <= T3_MIN_HOURS:
        return "T0"
    if hours_covered < T1_MIN_HOURS:
        return "T3"
    return "T1"


def occupancy_row(hours: float = 24.0, weight: float = 0.6):
    """Return a build_vitals.py-shaped input row for the use_utilization
    vital: (value 0-100, weight, tier, source, note)."""
    summary = occupancy_log.summarise(hours)
    rooms = summary["rooms"]
    hours_covered = summary["hours_covered"]
    tier = evidence_tier(hours_covered)

    if tier == "T0" or not rooms:
        return (0.0, weight, "T0", "occupancy_log (scenegraph time series)",
                "no occupancy.jsonl samples in the trailing %.0fh window" % hours)

    fractions = [r["utilisation_fraction"] for r in rooms.values()]
    peak_total = sum(r["peak_count"] for r in rooms.values())
    avg_util = sum(fractions) / len(fractions)
    value = max(0.0, min(100.0, avg_util * 100.0))

    caveat = ("SHORT WINDOW -- not enough duration to represent a real "
              "utilization pattern, treat as a proxy spot-check" if tier == "T3"
              else "sustained coverage, treated as direct-observation grade")
    note = ("%d room(s), %.2fh continuous coverage, %d samples, peak total "
            "%d people, mean utilisation %.1f%% -- %s"
            % (len(rooms), hours_covered, summary["total_samples"], peak_total,
               avg_util * 100.0, caveat))
    return (round(value, 1), weight, tier, "occupancy_log (scenegraph time series)", note)


def occupancy_summary_for_report(hours: float = 24.0) -> dict:
    """Convenience wrapper bundling the row + raw summary, for printing."""
    row = occupancy_row(hours)
    return {
        "row": {"value": row[0], "weight": row[1], "tier": row[2],
                "source": row[3], "note": row[4]},
        "raw_summary": occupancy_log.summarise(hours),
    }


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args()
    print(json.dumps(occupancy_summary_for_report(a.hours), indent=2))
