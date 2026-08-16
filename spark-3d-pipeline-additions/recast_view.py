"""Recast view -- "what could this building become," rendered as a
color-coded floor plan instead of a generated photo.

No image-generation model involved on purpose: nothing on the box today
does actual diffusion-based image generation (Cosmos is a reasoning/
understanding model, not a generator), and loading a new multi-GB model
stack (e.g. Stable Diffusion) carries real OOM risk given how tight memory
already runs this session. This gets to the same "what could this become"
answer using data and tools that already exist: real measured room
geometry (level1_rooms.json/level2_rooms.json) scored per-room against
candidate reuse profiles (same thresholds as build_vitals_v2.py's
future_use_fit, just applied per-room instead of building-wide).

Deterministic -- no VLM calls per room (would mean ~77 individual model
calls on an already memory-tight box). T3 tier: inferred from room size
only, not a code review.

Usage:
  python3 recast_view.py
"""
import os
import json
import numpy as np
import cv2

PLANS = os.path.expanduser("~/plans")

# Same profiles as build_vitals_v2.py's future_use_fit(), just scored per
# room instead of aggregated across the whole building.
PROFILES = {
    "office (as-is)":          {"range": (150, 600),  "color": (140, 140, 140)},   # gray (BGR) -- neutral, "unchanged"
    "multifamily/residential": {"range": (500, 1200), "color": (80, 200, 80)},     # green
    "school/classroom":        {"range": (700, 1100), "color": (200, 90, 220)},    # magenta
    "retail/mall":             {"range": (300, 3000), "color": (30, 130, 240)},    # orange
}


def clamp(v, lo=0.0, hi=100.0):
    return float(max(lo, min(hi, v)))


def room_fit(area_sqft):
    """Best-fit reuse category + score for ONE room, same rule as the
    building-wide heuristic in build_vitals_v2.py, applied per-room."""
    scored = {}
    for use, meta in PROFILES.items():
        lo, hi = meta["range"]
        if lo <= area_sqft <= hi:
            scored[use] = 100.0
        else:
            dist = min(abs(area_sqft - lo), abs(area_sqft - hi))
            scored[use] = clamp(100.0 - dist / 10.0)
    best = max(scored, key=scored.get)
    return best, scored[best]


def render_level(level, rooms):
    if not rooms:
        return None, {}
    all_pts = np.concatenate([np.array(r["poly"]) for r in rooms], axis=0)
    x0, y0 = all_pts[:, 0].min(), all_pts[:, 1].min()
    x1, y1 = all_pts[:, 0].max(), all_pts[:, 1].max()
    PPM = 30
    W = int((x1 - x0) * PPM) + 60
    H = int((y1 - y0) * PPM) + 60

    img = np.full((H, W, 3), 20, np.uint8)
    counts = {}
    for r in rooms:
        best, score = room_fit(r["area_sqft"])
        counts[best] = counts.get(best, 0) + 1
        poly = np.array(r["poly"])
        pts = np.stack([
            ((poly[:, 0] - x0) * PPM + 30).astype(int),
            (H - 30 - (poly[:, 1] - y0) * PPM).astype(int),
        ], -1)
        color = PROFILES[best]["color"]
        cv2.fillPoly(img, [pts], color)
        cv2.polylines(img, [pts], True, (255, 255, 255), 1)
        cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
        cv2.putText(img, "%.0f" % r["area_sqft"], (int(cx) - 16, int(cy)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1, cv2.LINE_AA)

    # legend
    ly = 20
    for use, meta in PROFILES.items():
        cv2.rectangle(img, (10, ly), (30, ly + 15), meta["color"], -1)
        cv2.putText(img, "%s (%d rooms)" % (use, counts.get(use, 0)), (36, ly + 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
        ly += 22

    return img, counts


def main():
    summary = {"schema": "recast_view_v1", "tier": "T3", "note":
               "deterministic room-size heuristic, not a code review", "levels": {}}
    for level in ("level1", "level2"):
        path = "%s/%s_rooms.json" % (PLANS, level)
        if not os.path.exists(path):
            continue
        rooms = json.load(open(path))
        img, counts = render_level(level, rooms)
        if img is None:
            continue
        out_path = "%s/%s_recast.png" % (PLANS, level)
        cv2.imwrite(out_path, img)
        summary["levels"][level] = {"room_count": len(rooms), "by_use": counts}
        print("%s -> %s  (%d rooms)" % (level, out_path, len(rooms)))
        for use, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print("   %-26s %d rooms" % (use, n))

    json.dump(summary, open("%s/recast_summary.json" % PLANS, "w"), indent=1)
    print("\nwritten -> %s/recast_summary.json" % PLANS)


if __name__ == "__main__":
    main()
