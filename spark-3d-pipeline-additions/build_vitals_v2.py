"""Build Vitals v2 — 7-category Building Health Index.

Category weights confirmed with the team 2026-08-15 (supersedes the 5-vital
v1 in build_vitals.py, kept alongside it for comparison, not deleted):

  economic                22%   sale comps, assessed value, mortgage/debt, grants, capital investment
  safety_suitability      20%   egress compliance + future-use fit from the measured plan
  usage_vacancy           15%   camera occupancy, electric usage, foot traffic
  physical_condition      15%   satellite/exterior imagery condition
  owner_media             10%   owner-submitted photos/video, resubmission triggers a rescore
  user_reviews_metadata   10%   tenant reviews + user-submitted context claims (validated only)
  neighborhood_safety      8%   crime, accidents near the building

Missing-evidence rule (unchanged from v1, stated explicitly per
responsibility(1).md: "Missing evidence produces gray or unknown, never a
fabricated zero"): a category's score is the weighted average over ONLY the
inputs that have evidence. A category's contribution to the overall BHI is
further discounted by its own evidence_coverage, so a category we have no
data for pulls the BHI toward "unknown," not toward "confirmed bad."

Data sources today: local pipeline outputs only (plan geometry, camera
occupancy already proven working this session). Everything else -- sale
comps, mortgage/debt, grants, satellite imagery, electric usage, foot
traffic, owner media, reviews, crime/accidents -- has no adapter connected
yet and is reported as T0 (no evidence), not fabricated. Swap the `fetch_*`
stubs for real adapters (Supabase or otherwise) once that data exists; the
scoring engine does not need to change.

Usage:
  python3 build_vitals_v2.py                # score using cached occupancy
  python3 build_vitals_v2.py --observe 120   # sample cameras for 120s first
"""
import os, sys, re, json, time, base64, argparse, urllib.request, urllib.error
import numpy as np

PLANS = os.path.expanduser("~/plans")
OUT = os.path.expanduser("~/plans/build_vitals_v2.json")

COSMOS_API = "http://127.0.0.1:30082/v1/chat/completions"
COSMOS_MODEL = "nvidia/cosmos3-nano-reasoner"

RECAST_BUILDING_ID = "king_county_wa:4088803750:profile"  # 1700 Westlake, from ~/recast DB
try:
    from recast_db import fetch_building_record
except ImportError:
    fetch_building_record = lambda building_id: None

BUILDING = {
    "name": "Lake Union Building",
    "address": "1700 Westlake Ave N, Seattle, WA 98109",
    "permit": "SDCI 6986025",
    "stories": 7,
    "occupancy_group": "B (office)",
    "construction_type": "IA",
}

CATEGORY_WEIGHTS = {
    "economic":              {"weight": 0.22, "label": "Economic Health"},
    "safety_suitability":    {"weight": 0.20, "label": "Safety & Suitability"},
    "usage_vacancy":         {"weight": 0.15, "label": "Usage / Vacancy"},
    "physical_condition":    {"weight": 0.15, "label": "Physical Condition"},
    "owner_media":           {"weight": 0.10, "label": "Owner-Submitted Media"},
    "user_reviews_metadata": {"weight": 0.10, "label": "User Reviews & Metadata"},
    "neighborhood_safety":   {"weight": 0.08, "label": "Neighborhood Safety"},
}
assert abs(sum(c["weight"] for c in CATEGORY_WEIGHTS.values()) - 1.0) < 1e-9, \
    "category weights must sum to 1.0"


def clamp(v, lo=0.0, hi=100.0):
    return float(max(lo, min(hi, v)))


# ---------------------------------------------------------------------------
# Real, working inputs (reused/adapted from build_vitals.py v1)
# ---------------------------------------------------------------------------

def observe(seconds):
    """Tier-1 occupancy evidence: sample the live cameras. Same method as v1."""
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    import cv2
    from ultralytics import YOLO
    streams = {
        "1F_COMMON_AREA": os.environ.get("RTSP_COMMON", ""),
        "2F_LOBBY":       os.environ.get("RTSP_LOBBY", ""),
        "2F_SW_HALLWAY":  os.environ.get("RTSP_SWHALL", ""),
    }
    model = YOLO("yolo11m.pt")
    caps = {k: cv2.VideoCapture(v, cv2.CAP_FFMPEG) for k, v in streams.items()}
    for c in caps.values():
        c.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    counts = {k: [] for k in streams}
    t_end = time.time() + seconds
    while time.time() < t_end:
        for k, c in caps.items():
            ok, fr = c.read()
            if not ok:
                continue
            r = model.predict(fr, imgsz=960, classes=[0], conf=0.35,
                              device=0, verbose=False)[0]
            counts[k].append(len(r.boxes))
        time.sleep(1.0)
    for c in caps.values():
        c.release()
    obs = {k: {"samples": len(v), "mean": float(np.mean(v)) if v else 0.0,
               "peak": int(max(v)) if v else 0}
           for k, v in counts.items()}
    json.dump(obs, open("%s/occupancy.json" % PLANS, "w"), indent=1)
    return obs


def load_rooms():
    """Real room polygons + areas from the plan pipeline (extract_plan.py ->
    plan2model.py). Returns (total_area_sqft, room_count, room_list)."""
    tot, rooms, all_rooms = 0.0, 0, []
    for lv in ("level1", "level2"):
        p = "%s/%s_rooms.json" % (PLANS, lv)
        if os.path.exists(p):
            rs = json.load(open(p))
            tot += sum(r["area_sqft"] for r in rs)
            rooms += len(rs)
            all_rooms.extend(rs)
    return tot, rooms, all_rooms


def _cosmos_vision_call(image_path, prompt, max_tokens=250, timeout=60):
    """Shared plumbing for calling the NVIDIA Cosmos reasoner
    (nvidia-cosmos3-reasoner, already running on the box for VSS) with an
    image. Returns the raw text response, or None if the call failed --
    callers must handle None by falling back to T0/heuristic, never by
    fabricating a number.
    """
    if not os.path.exists(image_path):
        return None
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception:
        return None
    payload = json.dumps({
        "model": COSMOS_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,%s" % b64}},
        ]}],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(COSMOS_API, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.load(r)
        return resp["choices"][0]["message"].get("content")
    except (urllib.error.URLError, KeyError, IndexError, TimeoutError):
        return None


def cosmos_condition_score(image_path):
    """Physical-condition rating from a real camera frame, via Cosmos.
    T3 (inferred) -- a VLM opinion, not a certified inspection. Returns
    None (never a fabricated number) if the call fails or the response
    doesn't contain a parseable 0-100 rating.
    """
    text = _cosmos_vision_call(
        image_path,
        "Rate the physical condition of this space from 0 to 100, where "
        "100 is pristine/well-maintained and 0 is severely damaged/derelict. "
        "Start your response with 'SCORE: <number>' then one sentence why.",
    )
    if not text:
        return None
    m = re.search(r"SCORE:\s*(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    return {"score": clamp(float(m.group(1))), "raw_response": text}


def cosmos_future_use_fit(floorplan_image_path):
    """Reasons directly over the actual floor plan image instead of the
    crude room-size heuristic below. T3 (inferred). Falls back to None on
    any failure -- caller should use the heuristic version instead, not
    silently skip the input.
    """
    text = _cosmos_vision_call(
        floorplan_image_path,
        "This is a measured floor plan of an office building, room areas "
        "labeled in square feet. Which reuse would fit best: multifamily "
        "residential, school/classroom, retail, or staying office? Start "
        "your response with 'BEST: <use>' then 'SCORE: <0-100>' then one "
        "sentence why.",
        max_tokens=200,
    )
    if not text:
        return None
    m_use = re.search(r"BEST:\s*([^\n]+)", text)
    m_score = re.search(r"SCORE:\s*(\d+(?:\.\d+)?)", text)
    if not (m_use and m_score):
        return None
    return {"best_use": m_use.group(1).strip(), "best_score": clamp(float(m_score.group(1))),
            "raw_response": text}


def future_use_fit(room_list):
    """Rule-based heuristic: how well the measured room sizes fit common
    reuse targets. T3 (inferred) -- placeholder thresholds, NOT a code
    review. Replace with real code-table lookups per jurisdiction before
    this becomes a claim made to anyone outside the team.
    """
    if not room_list:
        return None
    areas = [r["area_sqft"] for r in room_list]
    mean_area = sum(areas) / len(areas)
    profiles = {
        "office (as-is)":          (150, 600),
        "multifamily/residential": (500, 1200),
        "school/classroom":        (700, 1100),
        "retail/mall":             (300, 3000),
    }
    scored = {}
    for use, (lo, hi) in profiles.items():
        if lo <= mean_area <= hi:
            scored[use] = 100.0
        else:
            dist = min(abs(mean_area - lo), abs(mean_area - hi))
            scored[use] = clamp(100.0 - dist / 10.0)
    best_use = max(scored, key=scored.get)
    return {
        "best_use": best_use,
        "best_score": scored[best_use],
        "all_scores": {k: round(v, 1) for k, v in scored.items()},
        "note": "heuristic room-size fit only, not a code review; mean room %.0f sqft" % mean_area,
    }


# ---------------------------------------------------------------------------
# Category input builders. Each returns [(value, weight, tier, source, note), ...]
# for ALL inputs the category is defined to have -- including T0 placeholders
# for sources with no adapter connected yet, so evidence_coverage is honest.
# ---------------------------------------------------------------------------

def economic_inputs():
    db = fetch_building_record(RECAST_BUILDING_ID)

    inputs = [(0.0, 0.30, "T0", "sale price / neighborhood comps", "no comps feed connected")]

    if db and db.get("latest_assessed_value") and db.get("peak_assessed_value"):
        latest, peak = float(db["latest_assessed_value"]), float(db["peak_assessed_value"])
        val_score = clamp(100.0 * latest / peak) if peak else 0.0
        inputs.append((val_score, 0.25, "T2", "assessed value trend",
                        "King County assessor: $%s (%s) vs peak $%s -- %.1f%% of peak" %
                        (f"{latest:,.0f}", db.get("latest_assessment_year"), f"{peak:,.0f}",
                         100.0 * latest / peak if peak else 0)))
    else:
        inputs.append((0.0, 0.25, "T0", "assessed value trend", "no King County assessor feed"))

    inputs.append((0.0, 0.20, "T0", "mortgage / debt distress signal", "not connected"))
    inputs.append((0.0, 0.10, "T0", "federal / state grant eligibility",
                    "recast DB capital.program_fit has 0 rows for this building — not yet matched"))

    # Keep the independently-verified plan-PDF fact as the scored value --
    # the recast DB's permit_value_since_2019 shows $0 for this building,
    # which contradicts the actual approved TI permit we read directly off
    # the plan (6986025, approved 2023-09-14). That DB field looks
    # incomplete for this building, not evidence of zero investment -- not
    # trusting a number we know is wrong. DB's permit_rows count is cited
    # as corroborating context only.
    note = "tenant-improvement permit 6986025 approved 2023 — recent owner investment"
    if db and db.get("permit_rows"):
        note += " (recast DB shows %d permit records on file, though its dollar-value field is $0 " \
                "for this building — likely an incomplete load, contradicted by the plan PDF itself)" % db["permit_rows"]
    inputs.append((70.0, 0.15, "T2", "recent capital investment", note))

    return inputs


def physical_condition_inputs():
    inputs = [
        (0.0, 0.60, "T0", "satellite / exterior imagery condition",
         "no imagery adapter connected — needs Google/satellite source"),
    ]
    # Interior condition via Cosmos over a real captured camera frame. This
    # is NOT a substitute for exterior/satellite condition (different
    # question -- inside vs. outside) but it's real evidence we already
    # have, so it earns its own slice rather than waiting on satellite data.
    interior_photo = "%s/../arlo-frames/lobby.jpg" % PLANS
    cosmos = cosmos_condition_score(interior_photo) if os.path.exists(interior_photo) else None
    if cosmos:
        inputs.append((cosmos["score"], 0.40, "T3", "interior condition (Cosmos, lobby camera)",
                        "VLM opinion, not a certified inspection: %s" % cosmos["raw_response"][:160]))
    else:
        inputs.append((0.0, 0.40, "T0", "interior condition (Cosmos)", "Cosmos call failed or no photo available"))
    return inputs


def usage_vacancy_inputs(obs, area_sqft):
    inputs = []
    if obs:
        peak = sum(o["peak"] for o in obs.values())
        mean = sum(o["mean"] for o in obs.values())
        capacity = max(1.0, area_sqft / 150.0)
        util = clamp(100.0 * mean / capacity * 8.0)
        inputs.append((util, 0.55, "T1", "camera occupancy",
                       "mean %.1f people observed, peak %d, across %d cameras" % (mean, peak, len(obs))))
        ground = obs.get("1F_COMMON_AREA", {}).get("mean", 0.0)
        inputs.append((clamp(ground * 25.0), 0.15, "T1", "ground-floor activation",
                       "mean %.1f people on level 1 camera" % ground))
    else:
        inputs.append((0.0, 0.55, "T0", "camera occupancy", "no observation window run yet"))
        inputs.append((0.0, 0.15, "T0", "ground-floor activation", "no observation window run yet"))
    db = fetch_building_record(RECAST_BUILDING_ID)
    if db and db.get("energy_star_score") is not None:
        star = float(db["energy_star_score"])
        compliant = (db.get("compliance_status") or "").strip().lower() == "compliant"
        score = clamp(star - (0 if compliant else 10))
        inputs.append((score, 0.20, "T2", "energy benchmarking (Seattle OSE)",
                        "ENERGY STAR score %d, site EUI %s kBtu/sf, data year %s, compliance: %s" %
                        (star, db.get("site_eui_kbtu_sf"), db.get("energy_data_year"), db.get("compliance_status"))))
    else:
        inputs.append((0.0, 0.20, "T0", "electric usage trend", "no utility feed connected"))

    if db and db.get("availability_pct") is not None:
        avail_pct = float(db["availability_pct"])
        inputs.append((clamp(100.0 - avail_pct), 0.10, "T2", "availability signal",
                        "recast DB: %.1f%% available (confidence: %s)" % (avail_pct, db.get("availability_confidence"))))
    else:
        inputs.append((0.0, 0.10, "T0", "foot traffic / availability", "recast DB has no availability row for this building yet"))
    return inputs


def safety_suitability_inputs(area_sqft, rooms, room_list):
    inputs = [
        (85.0, 0.40, "T2", "life-safety compliance",
         "sheet A-002 egress approved 2023-09-14; Type IA; occupant load 563 documented"),
        (clamp(100.0 * rooms / 80.0), 0.30, "T1", "space subdivision",
         "%d rooms measured across 2 levels, %.0f sqft" % (rooms, area_sqft)),
    ]
    # Prefer Cosmos reasoning directly over the actual floor plan image;
    # fall back to the room-size heuristic if that call fails, so this
    # input degrades gracefully rather than going to T0 whenever the
    # vision endpoint is briefly unavailable.
    floorplan_img = "%s/level1_floorplan.png" % PLANS
    cosmos_fit = cosmos_future_use_fit(floorplan_img) if os.path.exists(floorplan_img) else None
    if cosmos_fit:
        inputs.append((cosmos_fit["best_score"], 0.30, "T3",
                        "future-use fit (%s, Cosmos over floor plan)" % cosmos_fit["best_use"],
                        cosmos_fit["raw_response"][:160]))
    else:
        fit = future_use_fit(room_list)
        if fit:
            inputs.append((fit["best_score"], 0.30, "T3", "future-use fit (%s, room-size heuristic)" % fit["best_use"], fit["note"]))
        else:
            inputs.append((0.0, 0.30, "T0", "future-use fit", "no room geometry or floor plan image available"))
    return inputs


def owner_media_inputs(building_id=None):
    # Stub: no owner submissions exist yet. Real adapter should:
    #   1. fetch_owner_media(building_id) -> list of {path, submitted_at}
    #   2. run each through content_authenticity.check() before counting
    #   3. resubmission should trigger a rescore, not just append
    return [
        (0.0, 1.00, "T0", "owner-submitted media", "no submissions yet — see content_authenticity.py"),
    ]


def user_reviews_inputs(building_id=None):
    # Stub: no reviews or user context claims exist yet. Real adapter should:
    #   - user context claims: only count if validated against a real public
    #     record (e.g. historic-registry lookup); unvalidated claims stay at
    #     T0/zero-weight, never move the score
    #   - reviews: aggregate with confidence rising with corroborating count,
    #     see review_confidence() below
    return [
        (0.0, 0.50, "T0", "user context claims (validated)", "no validated claims yet"),
        (0.0, 0.50, "T0", "tenant/customer reviews", "no reviews submitted yet"),
    ]


def review_confidence(num_matching_reviews):
    """Confidence multiplier for an aggregated review signal. One review is
    noise; corroborated reviews are signal. Caps at 0.9 so no volume of
    self-reported reviews alone reaches T1-equivalent certainty."""
    return min(0.9, 0.3 + 0.1 * num_matching_reviews)


def neighborhood_safety_inputs(building_id=None):
    return [
        (0.0, 0.60, "T0", "crime incidents near building", "no police-data feed connected"),
        (0.0, 0.40, "T0", "accident history + cause", "not connected"),
    ]


# ---------------------------------------------------------------------------
# Scoring engine (same coverage-weighted renormalize as v1's report())
# ---------------------------------------------------------------------------

def score_categories(obs, area_sqft, rooms, room_list):
    return {
        "economic": economic_inputs(),
        "physical_condition": physical_condition_inputs(),
        "usage_vacancy": usage_vacancy_inputs(obs, area_sqft),
        "safety_suitability": safety_suitability_inputs(area_sqft, rooms, room_list),
        "owner_media": owner_media_inputs(),
        "user_reviews_metadata": user_reviews_inputs(),
        "neighborhood_safety": neighborhood_safety_inputs(),
    }


def report(categories):
    card, bhi, covered_w = {}, 0.0, 0.0
    for key, meta in CATEGORY_WEIGHTS.items():
        rows = categories[key]
        graded = [(v, w, t, s, n) for (v, w, t, s, n) in rows if t != "T0"]
        wsum = sum(w for _, w, _, _, _ in graded)
        val = sum(v * w for v, w, _, _, _ in graded) / wsum if wsum else 0.0
        coverage = wsum / sum(w for _, w, _, _, _ in rows)
        card[key] = {
            "label": meta["label"], "score": round(val, 1),
            "weight": meta["weight"], "evidence_coverage": round(coverage, 2),
            "inputs": [{"source": s, "value": round(v, 1), "weight": w,
                        "tier": t, "note": n} for v, w, t, s, n in rows],
        }
        bhi += val * meta["weight"] * coverage
        covered_w += meta["weight"] * coverage
    bhi = bhi / covered_w if covered_w else 0.0
    return card, round(bhi, 1), round(covered_w, 2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--observe", type=int, default=0)
    a = ap.parse_args()

    if a.observe:
        print("observing cameras for %ds ..." % a.observe, flush=True)
        obs = observe(a.observe)
    elif os.path.exists("%s/occupancy.json" % PLANS):
        obs = json.load(open("%s/occupancy.json" % PLANS))
    else:
        obs = {}

    area, rooms, room_list = load_rooms()
    card, bhi, cov = report(score_categories(obs, area, rooms, room_list))

    out = {"building": BUILDING, "schema_version": "v2",
           "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "bhi": bhi, "evidence_coverage": cov,
           "measured_area_sqft": round(area, 1), "rooms": rooms,
           "occupancy_observed": obs, "categories": card}
    json.dump(out, open(OUT, "w"), indent=1)

    print("\n" + "=" * 70)
    print(" BUILD VITALS v2  —  %s" % BUILDING["name"])
    print(" %s   permit %s" % (BUILDING["address"], BUILDING["permit"]))
    print("=" * 70)
    print(" BUILDING HEALTH INDEX: %5.1f / 100     evidence coverage %.0f%%"
          % (bhi, cov * 100))
    print("-" * 70)
    for k, c in card.items():
        bar = "#" * int(c["score"] / 4)
        print(" %-26s %5.1f  w=%.2f  cov=%3.0f%%  %s"
              % (c["label"], c["score"], c["weight"], c["evidence_coverage"] * 100, bar))
        for i in c["inputs"]:
            print("     - [%s] %-32s %5.1f  %s" % (i["tier"], i["source"], i["value"], i["note"]))
    print("=" * 70)
    print("written -> %s" % OUT)
