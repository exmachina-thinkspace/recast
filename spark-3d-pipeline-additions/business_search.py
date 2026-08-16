"""Real active-business-license search near a building address, using
Seattle's public open dataset (Active Business License Tax Certificate,
data.seattle.gov, Socrata dataset wnbq-64tb). No API key required.

Closes the Community Engagement gap -- this is the "pedestrian counts /
business activity" input the original build_vitals.py schema already
expected but never had a real source for. Number of active, licensed
tenant businesses operating at a building's address is a genuine ground-
floor-activation signal: a building with many active going concerns is
meaningfully different from one with none, even without foot-traffic
sensors.

No lat/lon in this dataset -- matches on street address prefix instead
(exact building addresses share the same leading tokens, e.g. every
tenant at "1700 Westlake Ave N" has a street_address starting with that
string, suite number and all). Excludes pure ownership/leasing entities
(the landlord LLC itself, parking-lot operators) from the count so it
reflects tenant activity, not just who owns the building.

Usage:
  python3 business_search.py --address "1700 Westlake Ave N"
"""
import re
import json
import argparse
import urllib.request
import urllib.parse

API_BASE = "https://data.seattle.gov/resource/wnbq-64tb.json"

# NAICS descriptions that represent the building's own ownership/leasing
# structure, not an operating tenant -- excluded so the count reflects
# actual ground-floor activity.
OWNERSHIP_NAICS_HINTS = (
    "lessors of nonresidential buildings",
    "parking lots and garages",
    "lessors of residential buildings",
    "real estate property managers",
)


def search(address, timeout=15):
    """Real active businesses whose address starts with the given street
    address. Returns None on request failure -- never fabricates a count."""
    prefix = address.strip().upper()
    params = {
        "$where": "upper(street_address) like '%s%%'" % prefix.replace("'", "''"),
        "$select": "trade_name,naics_description,street_address",
        "$limit": "100",
    }
    url = API_BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "build-vitals-hackathon/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rows = json.load(r)
    except Exception:
        return None

    tenants = [r for r in rows if not any(h in (r.get("naics_description") or "").lower() for h in OWNERSHIP_NAICS_HINTS)]
    return {"total_licenses": len(rows), "tenant_count": len(tenants), "tenants": tenants}


def engagement_score(result):
    """Deterministic score from active tenant count. Saturates -- a
    building doesn't need 50 tenants to read as healthy, and this isn't
    trying to reward pure office-tower tenant density over a
    well-occupied smaller building. Illustrative calibration, not
    validated against actual foot traffic."""
    if result is None:
        return None
    return min(100.0, result["tenant_count"] * 6.0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    a = ap.parse_args()
    result = search(a.address)
    if result is None:
        print("request failed")
    else:
        print("total license records: %d, tenant businesses (ownership entities excluded): %d" %
              (result["total_licenses"], result["tenant_count"]))
        for t in result["tenants"][:10]:
            print("  %-40s %s" % (t["trade_name"][:40], t["naics_description"]))
        print("engagement_score:", round(engagement_score(result), 1))
