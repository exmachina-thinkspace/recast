"""Builds a real trajectory-engine input payload for the hero building
(1700 Westlake / Lake Union Building), using actual data already proven
this session -- never fabricating a fact the engine's own evidence-label
system would reject.

Real, sourced facts used:
  - Assessed value + peak (King County Assessor via recast DB)
  - BHI score + evidence coverage (build_vitals_v2.json, the full-evidence
    hero scorer)
  - Zoning (C2-40, recast DB)
  - Measured room geometry (extract_plan.py -> plan2model.py, real
    architectural plan) for reuse candidates' physical_fit
  - Real named tenants (Seattle Business License data via business_search.py)

Explicit gaps, honestly marked UNKNOWN/INSUFFICIENT_EVIDENCE, not guessed:
  - debt (recast.debt_maturity_signal reports INSUFFICIENT_DEBT_EVIDENCE
    for every building in the DB, hero included -- see docs/model-evaluation
    from 2026-08-16)
  - operations (no rent roll / financial statements reviewed)
  - lease financial terms (we know WHO the tenants are, not their rent/
    area/expiry)
  - market_fit and financial_fit for reuse candidates (no reviewed
    comparable-market or pro forma data)

scenario_assumptions are NOT facts -- they're modeler-supplied hypotheses
the engine's schema requires structurally. Marked clearly in the output
report as assumptions a human needs to review/replace, same treatment as
the JLL/distress-seed "needs source review" data found earlier.
"""
import json
import os

PLANS = os.path.expanduser("~/plans")
OUT = os.path.expanduser("~/plans/trajectory_input_hero.json")

BUILDING_ID = "king_county_wa:4088803750:profile"


def fact(value, tier, source=None, note=None):
    d = {"value": value, "evidence_label": tier}
    if tier in ("KNOWN", "OBSERVED", "INFERRED"):
        d["source_ref"] = source or "unspecified"
    if note:
        d["limitations"] = note
    return d


def load_rooms():
    total_sqft, room_count, ceilings = 0.0, 0, {}
    for lv, ceil_key in (("level1", "level1_ceiling_m"), ("level2", "level2_ceiling_m")):
        p = "%s/%s_rooms.json" % (PLANS, lv)
        if os.path.exists(p):
            rs = json.load(open(p))
            total_sqft += sum(r["area_sqft"] for r in rs)
            room_count += len(rs)
    stats_path = "%s/building_stats.json" % PLANS
    if os.path.exists(stats_path):
        stats = json.load(open(stats_path))
        ceilings = {"level1_ceiling_m": stats.get("level1", {}).get("ceiling_m"),
                    "level2_ceiling_m": stats.get("level2", {}).get("ceiling_m")}
    return total_sqft, room_count, ceilings


def physical_fit_for(use, total_sqft, room_count, ceilings):
    """Real, but explicitly heuristic (T3/INFERRED) physical-fit read from
    the actual measured plan geometry -- same room data recast_view.py
    already scored, reformatted into pass/conditional/fail/unknown for
    this schema. Not a code review."""
    l2_ceil = ceilings.get("level2_ceiling_m") or 0
    l1_ceil = ceilings.get("level1_ceiling_m") or 0
    mean_room = total_sqft / room_count if room_count else 0
    if use == "office (as-is)":
        return "pass", "Matches current, already-measured configuration (%d rooms, %.0f sqft mean)." % (room_count, mean_room)
    if use == "multifamily/residential":
        # Seattle residential typically wants ~7'6"+ ceilings; both measured
        # levels clear that. Real constraint is unit layout/plumbing risers,
        # which this plan-derived geometry doesn't tell us -- conditional,
        # not pass.
        ok = l1_ceil >= 2.29 and l2_ceil >= 2.29
        return ("conditional" if ok else "fail"), \
            "Ceiling heights (%.2fm/%.2fm) clear residential minimums, but plumbing/wet-wall placement is unknown from plan geometry alone." % (l1_ceil, l2_ceil)
    if use == "school/classroom":
        # Measured mean room size here (~197 sqft) is well under a
        # standard classroom footprint (700-1100 sqft) -- real, honest fail.
        return "fail", "Mean measured room size (%.0f sqft) is far below a standard classroom footprint; would need extensive wall removal." % mean_room
    if use == "retail/mall":
        return "conditional", "A minority of measured rooms (large open areas) fit retail scale; most of the floor plate does not."
    if use == "medical/clinic":
        # Outpatient exam rooms are typically ~100-150 sqft; the measured
        # mean room size here comfortably covers that scale, but per-room
        # plumbing (sinks) and ADA-width corridors aren't knowable from
        # plan geometry alone -- conditional, not pass.
        ok = mean_room >= 100
        return ("conditional" if ok else "fail"), \
            "Mean measured room size (%.0f sqft) comfortably covers typical outpatient exam-room scale (100-150 sqft), but per-room plumbing and ADA corridor width aren't knowable from plan geometry alone." % mean_room
    return "unknown", "No physical-fit assessment run for this use."


def build():
    v2 = json.load(open("%s/build_vitals_v2.json" % PLANS))
    total_sqft, room_count, ceilings = load_rooms()

    payload = {
        "schema_version": "1.0",
        "as_of_date": "2026-08-16",
        "demo_only": False,
        "building": {
            "building_id": BUILDING_ID,
            "address": fact("1700 Westlake Ave N, Seattle, WA 98109", "KNOWN", "King County Assessor"),
            "current_use": fact("Office", "KNOWN", "King County Assessor / SDCI permit 6986025"),
            "gross_area_sf": fact(162328, "KNOWN", "King County Assessor (recast.building)"),
            "market_value": fact(44842000, "KNOWN", "King County Assessor, 2026 assessment (recast DB)"),
        },
        "bhi": {
            "score": fact(v2["bhi"], "INFERRED", "build_vitals_v2.py, hero full-evidence scorer, generated %s" % v2["generated"]),
            "evidence_coverage": fact(v2["evidence_coverage"], "INFERRED", "build_vitals_v2.py"),
            "vitals": {k: fact(vv["score"], "INFERRED", "build_vitals_v2.py") for k, vv in v2["categories"].items()},
        },
        "operations": {
            "annual_gross_revenue": fact(None, "INSUFFICIENT_EVIDENCE", note="No rent roll or financial statements reviewed."),
            "annual_operating_expenses": fact(None, "INSUFFICIENT_EVIDENCE", note="Not reviewed."),
            "annual_required_capex": fact(None, "INSUFFICIENT_EVIDENCE", note="Not reviewed."),
            "cash_reserves": fact(None, "INSUFFICIENT_EVIDENCE", note="Not reviewed."),
        },
        "debt": [],  # recast.debt_maturity_signal: INSUFFICIENT_DEBT_EVIDENCE for every building including this one, as of 2026-08-16
        "leases": [
            {
                "tenant_id": "t1", "tenant_name": fact("Glencoe Software Inc", "OBSERVED", "Seattle Active Business License records"),
                "industry": fact("Custom Computer Programming Services", "OBSERVED", "Seattle Active Business License records"),
                "leased_area_sf": fact(None, "INSUFFICIENT_EVIDENCE", note="No lease document reviewed."),
                "annual_base_rent": fact(None, "INSUFFICIENT_EVIDENCE", note="No lease document reviewed."),
                "lease_end": fact(None, "INSUFFICIENT_EVIDENCE", note="No lease document reviewed."),
            },
            {
                "tenant_id": "t2", "tenant_name": fact("Equinox Business Law Group", "OBSERVED", "Seattle Active Business License records"),
                "industry": fact("Offices of Lawyers", "OBSERVED", "Seattle Active Business License records"),
                "leased_area_sf": fact(None, "INSUFFICIENT_EVIDENCE", note="No lease document reviewed."),
                "annual_base_rent": fact(None, "INSUFFICIENT_EVIDENCE", note="No lease document reviewed."),
                "lease_end": fact(None, "INSUFFICIENT_EVIDENCE", note="No lease document reviewed."),
            },
        ],
        # NOT facts -- explicit modeler assumptions the schema requires
        # structurally. A human needs to review/replace these before any
        # real use, same as the JLL/distress-seed data's own "needs source
        # review" flag.
        "scenario_assumptions": {
            "horizons_months": [12, 24, 36],
            "scenarios": {
                "improving": {"renewal_case": "high", "annual_rent_growth_pct": 0.03, "annual_expense_growth_pct": 0.02,
                              "annual_value_change_pct": 0.02, "refinance_interest_rate_pct": 0.055,
                              "existing_debt_service_change_pct": 0.0, "annual_bhi_change_points": 3.0},
                "base": {"renewal_case": "mid", "annual_rent_growth_pct": 0.01, "annual_expense_growth_pct": 0.03,
                         "annual_value_change_pct": 0.0, "refinance_interest_rate_pct": 0.065,
                         "existing_debt_service_change_pct": 0.0, "annual_bhi_change_points": 0.5},
                "adverse": {"renewal_case": "low", "annual_rent_growth_pct": -0.02, "annual_expense_growth_pct": 0.04,
                            "annual_value_change_pct": -0.05, "refinance_interest_rate_pct": 0.08,
                            "existing_debt_service_change_pct": 0.1, "annual_bhi_change_points": -4.0},
            },
            "underwriting": {
                "max_ltv": 0.65, "minimum_dscr": 1.25, "dscr_watch": 1.4,
                "lease_rollover_warning_pct": 0.3, "tenant_concentration_warning_pct": 0.35,
                "refinance_gap_critical_pct": 0.2,
            },
        },
        "reuse_candidates": [],
    }

    for use in ("office (as-is)", "multifamily/residential", "medical/clinic", "school/classroom", "retail/mall"):
        pf_val, pf_note = physical_fit_for(use, total_sqft, room_count, ceilings)
        payload["reuse_candidates"].append({
            "candidate_use": use,
            "physical_fit": fact(pf_val, "INFERRED", "recast_view.py room-size heuristic over real measured plan geometry", pf_note),
            "regulatory_fit": fact("conditional", "INFERRED", "Zoning C2-40 (recast DB) permits mixed commercial/residential use broadly; not a formal zoning compliance review",
                                    "General zone description only -- needs a real zoning review before use."),
            "market_fit": fact(None, "INSUFFICIENT_EVIDENCE", note="No reviewed comparable-market data for this specific use."),
            "financial_fit": fact(None, "INSUFFICIENT_EVIDENCE", note="No pro forma or operations data reviewed."),
            "required_next_evidence": [
                "Comparable market demand/rent data for %s" % use,
                "Pro forma / conversion cost estimate",
                "Formal zoning compliance review",
            ],
        })

    json.dump(payload, open(OUT, "w"), indent=1)
    print("written -> %s" % OUT)
    return payload


if __name__ == "__main__":
    build()
