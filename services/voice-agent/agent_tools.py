"""Tool implementations for the voice agent's function-calling loop.

Every tool wraps an already-proven-working module from earlier this
session -- crime_search.py, permit_search.py, business_search.py -- and
returns real data, never fabricated. Building lookup resolves by fuzzy
name/address match against the city-view-3d building list (B) and its
attached BHI records (BV), which covers the hero building plus 124 others.

This module expects crime_search.py, permit_search.py, business_search.py
to be importable (deployed alongside it in ~/plans/ on the box).
"""
import os
import re
import sys
import json

sys.path.insert(0, os.path.expanduser("~/plans"))
import crime_search
import permit_search
import business_search
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vision_tools  # new, additive -- image input (#2) and room-reuse (#3) tools

CITY_VIEW_HTML = os.path.expanduser("~/plans/city-view-3d/seattle-office-vitals-3d.html")

_cache = {"B": None, "BV": None}


def _load_city_data():
    if _cache["B"] is not None:
        return _cache["B"], _cache["BV"]
    src = open(CITY_VIEW_HTML, encoding="utf-8").read()
    mB = re.search(r"const B = (\[.*?\]);\n", src, re.S)
    mBV = re.search(r"const BV = (\{.*?\});\n", src, re.S)
    _cache["B"] = json.loads(mB.group(1))
    _cache["BV"] = json.loads(mBV.group(1))
    return _cache["B"], _cache["BV"]


def resolve_building(name_or_address):
    """Fuzzy match against building name or address. Returns None if no
    match -- callers must handle that, never guess a building."""
    B, BV = _load_city_data()
    q = name_or_address.strip().lower()
    if not q:
        return None
    # exact-ish match first, then substring
    for b in B:
        if (b.get("n") or "").lower() == q or b.get("a", "").lower() == q:
            return {"b": b, "bv": BV.get(str(b["i"]))}
    for b in B:
        if q in (b.get("n") or "").lower() or q in b.get("a", "").lower():
            return {"b": b, "bv": BV.get(str(b["i"]))}
    return None


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_bhi",
            "description": "Get the Building Health Index scorecard for a named building. "
                            "Covers the hero building (Lake Union Building) plus 124 other "
                            "Seattle downtown/SLU buildings. Returns overall BHI, evidence "
                            "coverage, and per-vital scores/tiers.",
            "parameters": {"type": "object", "properties": {
                "building_name": {"type": "string", "description": "Building name or street address, e.g. 'Norton Building' or '1700 Westlake'"},
            }, "required": ["building_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_crime",
            "description": "Real Seattle Police Department crime incidents near a building "
                            "in the last ~12 months. Use when asked about safety, crime, or "
                            "what's happened nearby.",
            "parameters": {"type": "object", "properties": {
                "building_name": {"type": "string"},
                "radius_m": {"type": "number", "description": "Search radius in meters, default 400"},
            }, "required": ["building_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_permits",
            "description": "Real Seattle building permits (completed, with a real dollar "
                            "value) near a building. Use when asked about renovations, "
                            "capital investment, or upkeep.",
            "parameters": {"type": "object", "properties": {
                "building_name": {"type": "string"},
                "radius_m": {"type": "number", "description": "Search radius in meters, default 100"},
            }, "required": ["building_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_businesses",
            "description": "Real active Seattle business license records at a building's "
                            "address. Use when asked about tenants, occupants, or what "
                            "businesses operate there.",
            "parameters": {"type": "object", "properties": {
                "building_name": {"type": "string"},
            }, "required": ["building_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_camera_view",
            "description": "Get a description of a known captured camera view (lobby, "
                            "common_area, or sw_hallway) from the hero building, using vision "
                            "AI. This is a captured frame, not a live feed. Use when asked "
                            "what a space looks like or its condition.",
            "parameters": {"type": "object", "properties": {
                "zone": {"type": "string", "enum": ["lobby", "common_area", "sw_hallway"]},
                "question": {"type": "string", "description": "Optional specific question about the image"},
            }, "required": ["zone"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "whats_next_for_building",
            "description": "For the hero building, get a grounded description of what the "
                            "building's rooms could become (reuse candidates like residential, "
                            "school, retail) based on real measured room geometry, plus a "
                            "vision-AI description of what that conversion would realistically "
                            "require. Use when asked 'what could this become' or about reuse/conversion.",
            "parameters": {"type": "object", "properties": {
                "target_use": {"type": "string", "description": "Optional specific use to ask about, e.g. 'multifamily/residential'"},
            }, "required": []},
        },
    },
]


def call_tool(name, args):
    """Dispatch a tool call. Returns a JSON-serializable dict. Never
    raises -- errors come back as {"error": ...} so the model can react
    to them instead of the request failing."""
    try:
        if name == "get_bhi":
            match = resolve_building(args["building_name"])
            if not match:
                return {"error": "no building found matching '%s'" % args["building_name"]}
            b, bv = match["b"], match["bv"]
            if not bv:
                return {"building": b.get("n") or b["a"], "address": b["a"],
                        "status": "INSUFFICIENT_EVIDENCE", "note": "no BHI record for this building yet"}
            vitals_summary = {k: {"score": v["score"], "weight": v["weight"], "evidence_coverage": v["evidence_coverage"]}
                               for k, v in bv["vitals"].items()}
            return {"building": b.get("n") or b["a"], "address": b["a"],
                    "bhi": bv["bhi"], "evidence_coverage": bv["evidence_coverage"], "vitals": vitals_summary}

        elif name == "search_crime":
            match = resolve_building(args["building_name"])
            if not match or not match["b"].get("la"):
                return {"error": "no coordinates for building '%s'" % args["building_name"]}
            b = match["b"]
            radius = args.get("radius_m", 400)
            result = crime_search.near(b["la"], b["lo"], radius_m=radius)
            return {"building": b.get("n") or b["a"], "radius_m": radius,
                    "incident_count": result["count"], "by_category": result["by_category"],
                    "safety_score": round(crime_search.safety_score(result), 1),
                    "note": "SPD data, last ~12mo, relative comparison only, not a validated crime-risk score"}

        elif name == "search_permits":
            match = resolve_building(args["building_name"])
            if not match or not match["b"].get("la"):
                return {"error": "no coordinates for building '%s'" % args["building_name"]}
            b = match["b"]
            radius = args.get("radius_m", 100)
            result = permit_search.near(b["la"], b["lo"], radius_m=radius)
            return {"building": b.get("n") or b["a"], "radius_m": radius,
                    "permit_count": result["count"], "total_cost": result["total_cost"],
                    "upkeep_score": round(permit_search.upkeep_score(result), 1)}

        elif name == "search_businesses":
            match = resolve_building(args["building_name"])
            if not match:
                return {"error": "no building found matching '%s'" % args["building_name"]}
            b = match["b"]
            result = business_search.search(b["a"])
            if result is None:
                return {"error": "business license lookup failed"}
            return {"building": b.get("n") or b["a"], "tenant_count": result["tenant_count"],
                    "total_license_records": result["total_licenses"],
                    "sample_tenants": [t["trade_name"] for t in result["tenants"][:8]]}

        elif name == "describe_camera_view":
            return vision_tools.describe_camera_frame(args["zone"], args.get("question"))

        elif name == "whats_next_for_building":
            return vision_tools.whats_next(args.get("target_use"))

        else:
            return {"error": "unknown tool: %s" % name}
    except Exception as e:
        return {"error": "tool execution failed: %s" % e}
