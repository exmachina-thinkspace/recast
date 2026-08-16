"""Generates BV records (build_vitals.py v1 shape) for the city-view-3d
citywide map, for the hero building (1700 Westlake, full evidence: camera,
plan geometry, Cosmos reasoning) plus >=20 other South Lake Union / Downtown
buildings (records-only evidence: leasing %, energy benchmarking from the
page's own embedded data, plus assessed value trend from the recast DB
where an address match exists).

This deliberately produces uneven evidence coverage across buildings -- the
hero building will show far more tiers filled in than the records-only
ones. That's not a bug: it's the actual "records-only vs. sensor-corrected"
thesis of the whole project, made visible on one map.

Never invents a value for a building with no evidence -- unmatched/missing
fields are T0 and get excluded from that vital's average by the page's own
bhi() function, same discipline as build_vitals_v2.py.

Usage:
  python3 generate_city_bhi.py --html ~/plans/city-view-3d/seattle-office-vitals-3d.html
  # writes new_building.json (hero, for --var B --merge)
  # and    build_vitals_all.json (BV records, for --var BV --merge)
"""
import os
import re
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recast_db import fetch_building_record
import crime_search
import business_search
import permit_search
import math

HERO_ADDRESS = "1700 Westlake Ave N"
HERO_BUILDING_ID = "king_county_wa:4088803750:profile"


def clamp(v, lo=0.0, hi=100.0):
    return float(max(lo, min(hi, v)))


# Vital weights (v1 5-vital shape). Leasing/occupancy set to 40% of BHI per
# team direction 2026-08-15 -- it lives inside Use / Utilization (leased %
# for records-only buildings, camera occupancy for the hero building), not
# as a separate top-level vital, since that's what the vital already means.
# Remaining 60% split proportionally across the other four from their
# original 0.20/0.20/0.15/0.20 weights (scale factor 0.6/0.75 = 0.8).
VITAL_WEIGHTS = {
    "use_utilization": 0.40,
    "clean_safety": 0.16,
    "economic": 0.16,
    "community": 0.12,
    "productivity_upkeep": 0.16,
}
assert abs(sum(VITAL_WEIGHTS.values()) - 1.0) < 1e-9


def load_B(html_path):
    src = open(html_path, encoding="utf-8").read()
    m = re.search(r"const B = (\[.*?\]);\n", src, re.S)
    return json.loads(m.group(1))


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def load_recast_coords():
    """All 69 recast.building rows with real, clean numeric coordinates --
    fetched once and matched by proximity, not fragile text matching
    against the DB's fixed-width-padded address field (the bug found and
    fixed earlier only patched whitespace; text matching still misses
    plenty of real matches from abbreviation/ordering differences)."""
    from recast_db import _connect
    conn = _connect()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT building_id, latitude, longitude FROM recast.building "
                        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
            return [{"building_id": r[0], "lat": float(r[1]), "lon": float(r[2])} for r in cur.fetchall()]
    finally:
        conn.close()


def match_by_proximity(lat, lon, recast_coords, max_m=60):
    if lat is None or lon is None or not recast_coords:
        return None
    best, best_d = None, max_m
    for r in recast_coords:
        d = _haversine_m(lat, lon, r["lat"], r["lon"])
        if d < best_d:
            best, best_d = r["building_id"], d
    return best


def fetch_building_details(building_id):
    """Full recast DB record for a KNOWN building_id (already matched by
    proximity -- see match_by_proximity). Pulls real, individual permit
    values (not the buggy per-building aggregate that showed $0 for the
    hero building) and the energy compliance flag, a genuine regulatory
    record, not a proxy."""
    from recast_db import _connect
    conn = _connect()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.building_id, b.source_parcel_id,
                       t.latest_assessed_value, t.peak_assessed_value, t.latest_assessment_year,
                       e.latest_compliance_status,
                       d.debt_maturity_state, d.evidence_tier, d.next_verification_step
                FROM recast.building b
                LEFT JOIN recast.building_value_trajectory t ON t.building_id = b.building_id
                LEFT JOIN recast.building_energy_signal e ON e.building_id = b.building_id
                LEFT JOIN recast.debt_maturity_signal d ON d.building_id = b.building_id
                WHERE b.building_id = %s
            """, (building_id,))
            row = cur.fetchone()
            if not row:
                return None
            match = dict(zip(["building_id", "source_parcel_id", "latest_assessed_value",
                               "peak_assessed_value", "latest_assessment_year",
                               "compliance_status", "debt_maturity_state", "debt_evidence_tier",
                               "debt_next_step"], row))

            if match["source_parcel_id"]:
                cur.execute("""
                    SELECT permit_type, permit_status, permit_value
                    FROM source_outerspaces.permit_history_subset
                    WHERE source_parcel_id = %s AND permit_status = 'Complete' AND permit_value > 0
                """, (match["source_parcel_id"],))
                permits = cur.fetchall()
                match["completed_permit_value_total"] = sum(p[2] for p in permits) if permits else 0
                match["completed_permit_count"] = len(permits)
            else:
                match["completed_permit_value_total"] = 0
                match["completed_permit_count"] = 0
            return match
    except Exception:
        return None
    finally:
        conn.close()


def records_only_vitals(b, db_match, incidents=None, permits=None):
    """v1 5-vital shape for a records-only building (no camera/plan data).
    b is one B-array entry (has l=leased%, e=EUI, s=ENERGY STAR, y=year)."""
    leased = b.get("l")
    eui = b.get("e")

    # Leasing is the dominant input here -- it's the real, direct occupancy
    # record (T2, availability report), carrying most of this vital's now
    # much larger 40% share of the BHI. EUI stays as a minor supporting
    # proxy, not the primary evidence.
    use_inputs = []
    if leased is not None:
        use_inputs.append({"source": "occupied space (availability report)", "value": round(clamp(leased), 1),
                            "weight": 0.8, "tier": "T2",
                            "note": "%.1f%% leased, Aug 2026 availability report" % leased})
    else:
        use_inputs.append({"source": "occupied space", "value": 0.0, "weight": 0.8, "tier": "T0",
                            "note": "not in availability report"})
    if eui is not None:
        # Lower EUI is usually more efficient, not directly occupancy --
        # noted honestly as a proxy, not a direct occupancy measurement.
        eui_score = clamp(100.0 - eui * 0.6)
        use_inputs.append({"source": "energy use intensity (proxy)", "value": round(eui_score, 1),
                            "weight": 0.2, "tier": "T3",
                            "note": "site EUI %.1f kBtu/sf -- efficiency proxy, not a direct occupancy measurement" % eui})
    else:
        use_inputs.append({"source": "energy use intensity", "value": 0.0, "weight": 0.2, "tier": "T0",
                            "note": "no Seattle Building Energy Benchmarking match"})

    clean_inputs = [{"source": "life-safety compliance", "value": 0.0, "weight": 0.34, "tier": "T0",
                      "note": "no plan-derived egress data at city scale"}]
    if db_match and db_match.get("compliance_status"):
        compliant = db_match["compliance_status"].strip().lower() == "compliant"
        clean_inputs.append({"source": "energy code compliance (Seattle BEPS)",
                              "value": 100.0 if compliant else 40.0, "weight": 0.33, "tier": "T2",
                              "note": "Seattle Building Emissions Performance Standard: %s" % db_match["compliance_status"]})
    else:
        clean_inputs.append({"source": "energy code compliance (Seattle BEPS)", "value": 0.0, "weight": 0.33, "tier": "T0",
                              "note": "no recast DB match for this address"})
    if incidents is not None and b.get("la") and b.get("lo"):
        crime = crime_search.near(b["la"], b["lo"], radius_m=400, incidents=incidents)
        score = crime_search.safety_score(crime)
        top = sorted(crime["by_category"].items(), key=lambda kv: -kv[1])[:2]
        top_str = ", ".join("%d %s" % (n, c.lower()) for c, n in top) if top else "none"
        clean_inputs.append({"source": "nearby crime incidents (SPD, 400m, 12mo)", "value": round(score, 1),
                              "weight": 0.33, "tier": "T2",
                              "note": "%d incidents within 400m in the last year (%s); relative comparison only, "
                                      "not a validated crime-risk score" % (crime["count"], top_str)})
    else:
        clean_inputs.append({"source": "nearby crime incidents", "value": 0.0, "weight": 0.33, "tier": "T0",
                              "note": "no coordinates or crime data available"})

    # Leasing lives in Use / Utilization now (see above) -- not repeated
    # here, to avoid double-counting the same fact in two vitals.
    econ_inputs = []
    if db_match and db_match.get("latest_assessed_value") and db_match.get("peak_assessed_value"):
        latest, peak = float(db_match["latest_assessed_value"]), float(db_match["peak_assessed_value"])
        val_score = clamp(100.0 * latest / peak) if peak else 0.0
        econ_inputs.append({"source": "assessed value trend", "value": round(val_score, 1), "weight": 0.7,
                             "tier": "T2", "note": "King County assessor: $%s (%s) vs peak $%s" %
                             (f"{latest:,.0f}", db_match.get("latest_assessment_year"), f"{peak:,.0f}")})
    else:
        econ_inputs.append({"source": "assessed value trend", "value": 0.0, "weight": 0.7, "tier": "T0",
                             "note": "no recast DB match for this address"})

    # recast.debt_maturity_signal (added 2026-08-16) -- checks a real table,
    # but every row is deliberately INSUFFICIENT_DEBT_EVIDENCE right now.
    # The team's own workflow doc is explicit: JLL/distress-seed leads do
    # not become verified debt facts until recorder/court/licensed-source
    # review is complete. Stays T0 until that review happens -- this input
    # exists so the score updates automatically the moment real debt
    # evidence lands, without needing another code change.
    debt_state = db_match.get("debt_maturity_state") if db_match else None
    if debt_state and debt_state != "INSUFFICIENT_DEBT_EVIDENCE":
        econ_inputs.append({"source": "debt maturity signal", "value": 50.0, "weight": 0.3, "tier": "T2",
                             "note": "recast.debt_maturity_signal: %s (%s)" %
                                     (debt_state, db_match.get("debt_next_step") or "")})
    elif debt_state:
        econ_inputs.append({"source": "debt maturity signal", "value": 0.0, "weight": 0.3, "tier": "T0",
                             "note": "recast.debt_maturity_signal: INSUFFICIENT_DEBT_EVIDENCE -- "
                                     "recorder/court source review not yet done, per team's own workflow"})
    else:
        econ_inputs.append({"source": "debt maturity signal", "value": 0.0, "weight": 0.3, "tier": "T0",
                             "note": "no recast DB match for this address"})

    community_inputs = [{"source": "ground-floor activation (camera)", "value": 0.0, "weight": 0.3, "tier": "T0",
                          "note": "no camera data at city scale"}]
    biz = business_search.search(b["a"])
    if biz is not None:
        score = business_search.engagement_score(biz)
        community_inputs.append({"source": "active business licenses on file", "value": round(score, 1),
                                  "weight": 0.7, "tier": "T2",
                                  "note": "%d active tenant businesses at this address (Seattle business license data), "
                                          "%d total license records including ownership entities" %
                                          (biz["tenant_count"], biz["total_licenses"])})
    else:
        community_inputs.append({"source": "active business licenses", "value": 0.0, "weight": 0.7, "tier": "T0",
                                  "note": "business license lookup failed or returned nothing"})

    # SDCI permits (data.seattle.gov, all Seattle addresses) is the primary
    # source now -- covers every building, not just the ~30 with a recast
    # DB match. Recast DB permit data, when present, is kept as a second,
    # corroborating input rather than dropped.
    prod_inputs = []
    if permits is not None and b.get("la") and b.get("lo"):
        nearby = permit_search.near(b["la"], b["lo"], radius_m=100, permits=permits)
        score = permit_search.upkeep_score(nearby)
        prod_inputs.append({"source": "completed capital investment (SDCI permits, 100m)", "value": round(score, 1),
                             "weight": 0.7, "tier": "T2",
                             "note": "%d completed permits within 100m totaling $%s (Seattle Building Permits data)" %
                                     (nearby["count"], f"{nearby['total_cost']:,.0f}")})
    else:
        prod_inputs.append({"source": "completed capital investment (SDCI permits)", "value": 0.0, "weight": 0.7, "tier": "T0",
                             "note": "no coordinates or permit data available"})

    if db_match and db_match.get("completed_permit_count"):
        total_value = float(db_match["completed_permit_value_total"])
        n = db_match["completed_permit_count"]
        score = clamp(100.0 * min(1.0, total_value / 2_000_000.0))
        prod_inputs.append({"source": "completed capital investment (recast DB permits)", "value": round(score, 1),
                             "weight": 0.3, "tier": "T2",
                             "note": "%d completed permits totaling $%s on file (recast DB, corroborating source)" %
                                     (n, f"{total_value:,.0f}")})
    else:
        prod_inputs.append({"source": "completed capital investment (recast DB permits)", "value": 0.0, "weight": 0.3, "tier": "T0",
                             "note": "no recast DB match for this address"})

    def vital(label, weight, inputs):
        graded = [i for i in inputs if i["tier"] != "T0"]
        wsum = sum(i["weight"] for i in graded)
        score = sum(i["value"] * i["weight"] for i in graded) / wsum if wsum else 0.0
        coverage = wsum / sum(i["weight"] for i in inputs) if inputs else 0.0
        return {"label": label, "score": round(score, 1), "weight": weight,
                "evidence_coverage": round(coverage, 2), "inputs": inputs}

    vitals = {
        "use_utilization": vital("Use / Utilization", VITAL_WEIGHTS["use_utilization"], use_inputs),
        "clean_safety": vital("Clean & Safety", VITAL_WEIGHTS["clean_safety"], clean_inputs),
        "economic": vital("Economic Sustainability", VITAL_WEIGHTS["economic"], econ_inputs),
        "community": vital("Community Engagement", VITAL_WEIGHTS["community"], community_inputs),
        "productivity_upkeep": vital("Productivity & Upkeep", VITAL_WEIGHTS["productivity_upkeep"], prod_inputs),
    }
    bhi, covered_w = 0.0, 0.0
    for v in vitals.values():
        bhi += v["score"] * v["weight"] * v["evidence_coverage"]
        covered_w += v["weight"] * v["evidence_coverage"]
    bhi = bhi / covered_w if covered_w else 0.0
    return {"bhi": round(bhi, 1), "evidence_coverage": round(covered_w, 2), "vitals": vitals}


def hero_vitals_from_v2(v2_path, hero_lat=None, hero_lon=None, incidents=None, permits=None):
    """Map build_vitals_v2.json (our full-evidence hero scorer) onto the
    v1 5-vital shape this page expects."""
    v2 = json.load(open(v2_path))
    cat = v2["categories"]

    def v2_inputs(cat_key):
        return [{"source": i["source"], "value": i["value"], "weight": i["weight"],
                  "tier": i["tier"], "note": i["note"]} for i in cat[cat_key]["inputs"]]

    use_inputs = [i for i in v2_inputs("usage_vacancy") if "camera occupancy" in i["source"]] or \
                 [{"source": "camera occupancy", "value": 0.0, "weight": 1.0, "tier": "T0", "note": "no observation"}]
    clean_inputs = [i for i in v2_inputs("safety_suitability") if "life-safety" in i["source"]]
    clean_inputs += [i for i in v2_inputs("physical_condition") if "interior condition" in i["source"]]
    if incidents is not None and hero_lat and hero_lon:
        crime = crime_search.near(hero_lat, hero_lon, radius_m=400, incidents=incidents)
        score = crime_search.safety_score(crime)
        top = sorted(crime["by_category"].items(), key=lambda kv: -kv[1])[:2]
        top_str = ", ".join("%d %s" % (n, c.lower()) for c, n in top) if top else "none"
        clean_inputs.append({"source": "nearby crime incidents (SPD, 400m, 12mo)", "value": round(score, 1),
                              "weight": sum(i["weight"] for i in clean_inputs) or 1.0, "tier": "T2",
                              "note": "%d incidents within 400m in the last year (%s); relative comparison only, "
                                      "not a validated crime-risk score" % (crime["count"], top_str)})
    econ_inputs = v2_inputs("economic")
    community_inputs = [i for i in v2_inputs("usage_vacancy") if "ground-floor" in i["source"]] or \
                        [{"source": "ground-floor activation (camera)", "value": 0.0, "weight": 1.0, "tier": "T0", "note": "no observation"}]
    for ci in community_inputs:
        ci["weight"] = 0.3
    biz = business_search.search(HERO_ADDRESS)
    if biz is not None:
        score = business_search.engagement_score(biz)
        community_inputs.append({"source": "active business licenses on file", "value": round(score, 1),
                                  "weight": 0.7, "tier": "T2",
                                  "note": "%d active tenant businesses at this address (Seattle business license data), "
                                          "%d total license records including ownership entities" %
                                          (biz["tenant_count"], biz["total_licenses"])})
    else:
        community_inputs.append({"source": "active business licenses", "value": 0.0, "weight": 0.7, "tier": "T0",
                                  "note": "business license lookup failed or returned nothing"})
    prod_inputs = [i for i in v2_inputs("safety_suitability") if "space subdivision" in i["source"] or "future-use" in i["source"]]
    for pi in prod_inputs:
        pi["weight"] = 0.3
    if permits is not None and hero_lat and hero_lon:
        nearby = permit_search.near(hero_lat, hero_lon, radius_m=100, permits=permits)
        score = permit_search.upkeep_score(nearby)
        prod_inputs.append({"source": "completed capital investment (SDCI permits, 100m)", "value": round(score, 1),
                             "weight": 0.4, "tier": "T2",
                             "note": "%d completed permits within 100m totaling $%s (Seattle Building Permits data)" %
                                     (nearby["count"], f"{nearby['total_cost']:,.0f}")})

    def vital(label, weight, inputs):
        graded = [i for i in inputs if i["tier"] != "T0"]
        wsum = sum(i["weight"] for i in graded)
        score = sum(i["value"] * i["weight"] for i in graded) / wsum if wsum else 0.0
        coverage = wsum / sum(i["weight"] for i in inputs) if inputs else 0.0
        return {"label": label, "score": round(score, 1), "weight": weight,
                "evidence_coverage": round(coverage, 2), "inputs": inputs}

    vitals = {
        "use_utilization": vital("Use / Utilization", VITAL_WEIGHTS["use_utilization"], use_inputs),
        "clean_safety": vital("Clean & Safety", VITAL_WEIGHTS["clean_safety"], clean_inputs),
        "economic": vital("Economic Sustainability", VITAL_WEIGHTS["economic"], econ_inputs),
        "community": vital("Community Engagement", VITAL_WEIGHTS["community"], community_inputs),
        "productivity_upkeep": vital("Productivity & Upkeep", VITAL_WEIGHTS["productivity_upkeep"], prod_inputs),
    }
    bhi, covered_w = 0.0, 0.0
    for v in vitals.values():
        bhi += v["score"] * v["weight"] * v["evidence_coverage"]
        covered_w += v["weight"] * v["evidence_coverage"]
    bhi = bhi / covered_w if covered_w else 0.0
    return {"bhi": round(bhi, 1), "evidence_coverage": round(covered_w, 2), "vitals": vitals}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--n", type=int, default=24, help="how many records-only buildings to score")
    ap.add_argument("--all", action="store_true", help="score every non-hero building in B, ignoring --n")
    ap.add_argument("--v2", default=os.path.expanduser("~/plans/build_vitals_v2.json"))
    ap.add_argument("--out-dir", default=".")
    a = ap.parse_args()

    B = load_B(a.html)
    existing_hero = next((b for b in B if HERO_ADDRESS.split()[0] in b["a"] and "Westlake" in b["a"]), None)
    hero_i = existing_hero["i"] if existing_hero else max(b["i"] for b in B) + 1

    print("loading SPD crime data (cached, refreshes every 24h)...")
    incidents = crime_search.load_incidents()
    print("  %d incidents loaded (West precinct, ~12mo)" % len(incidents))

    print("loading recast DB coordinates for proximity matching...")
    recast_coords = load_recast_coords()
    print("  %d recast buildings with coordinates" % len(recast_coords))

    print("loading SDCI building permits (cached, refreshes every 24h)...")
    permits = permit_search.load_permits()
    print("  %d completed+valued permits loaded (downtown/SLU bbox)" % len(permits))

    if a.all:
        chosen = [b for b in B if b["i"] != hero_i]
    else:
        # pick a spread: some with real leasing+energy data (colorful demo),
        # a couple with nothing (legitimately shows gray/insufficient evidence)
        with_data = [b for b in B if b.get("l") is not None and b.get("e") is not None]
        no_data = [b for b in B if b.get("l") is None or b.get("e") is None]
        chosen = with_data[: max(0, a.n - 3)] + no_data[:3]
        chosen = chosen[: a.n]

    bv = {}
    for b in chosen:
        matched_id = match_by_proximity(b.get("la"), b.get("lo"), recast_coords)
        db_match = fetch_building_details(matched_id) if matched_id else None
        bv[str(b["i"])] = {
            "building": {"name": b.get("n") or b["a"], "address": b["a"] + ", Seattle, WA"},
            "generated": "records-only-batch",
            **records_only_vitals(b, db_match, incidents, permits),
        }
        print("scored #%d %-40s bhi=%s cov=%s%s" % (
            b["i"], (b.get("n") or b["a"])[:40], bv[str(b["i"])]["bhi"], bv[str(b["i"])]["evidence_coverage"],
            "  [DB match]" if db_match else ""))

    hero_new_B_entry = None
    if os.path.exists(a.v2):
        hero_record = hero_vitals_from_v2(a.v2, hero_lat=47.6339462747, hero_lon=-122.3396205208, incidents=incidents, permits=permits)
        if not existing_hero:
            hero_new_B_entry = {"i": hero_i, "a": HERO_ADDRESS, "n": "Lake Union Building",
                                 "la": 47.6339462747, "lo": -122.3396205208,
                                 "r": None, "l": None, "y": 1970, "e": 61.0, "g": 162328, "u": "Office",
                                 "nb": "LAKE UNION", "m": "exact", "s": 66,
                                 "ma": HERO_ADDRESS, "mn": "Lake Union Building", "my": "1970", "f": 7}
        bv[str(hero_i)] = {"building": {"name": "Lake Union Building", "address": HERO_ADDRESS + ", Seattle, WA 98109"},
                            "generated": "hero-full-evidence", **hero_record}
        print("scored #%d Lake Union Building (HERO, full evidence)%s bhi=%s cov=%s" %
              (hero_i, "" if existing_hero else " [NEW]", hero_record["bhi"], hero_record["evidence_coverage"]))
    else:
        print("WARNING: %s not found, hero building not added" % a.v2, file=sys.stderr)

    os.makedirs(a.out_dir, exist_ok=True)
    json.dump(bv, open(os.path.join(a.out_dir, "build_vitals_all.json"), "w"), indent=1)
    if hero_new_B_entry:
        json.dump([hero_new_B_entry], open(os.path.join(a.out_dir, "new_building.json"), "w"), indent=1)
    print("\nwritten -> %s/build_vitals_all.json (%d records)" % (a.out_dir, len(bv)))
    if hero_new_B_entry:
        print("written -> %s/new_building.json (hero building #%d)" % (a.out_dir, hero_i))


if __name__ == "__main__":
    main()
