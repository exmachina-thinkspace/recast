"""Real building-permit search near a building, using Seattle's public
Issued Building Permits dataset (data.seattle.gov, Socrata dataset
76t5-zqzr). No API key required. Covers every permit since 1990 with real
lat/lon and actual project cost -- unlike the recast DB (only 69 buildings,
capped Productivity & Upkeep coverage at ~28 of 125), this dataset covers
every Seattle address directly, closing that gap for the whole city.

Fetches once for the downtown/SLU bounding box (cached 24h), then matches
by real proximity per building, same pattern as crime_search.py.

Usage:
  python3 permit_search.py --lat 47.6339 --lon -122.3396 --radius-m 150
"""
import os
import json
import math
import time
import argparse
import urllib.request
import urllib.parse

CACHE_PATH = os.path.expanduser("~/plans/city-view-3d/data/sdci_permits.json")
CACHE_MAX_AGE_S = 24 * 3600
API_BASE = "https://data.seattle.gov/resource/76t5-zqzr.json"

# Downtown/SLU/Queen Anne bounding box -- covers this project's building set.
BBOX = (47.595, 47.645, -122.365, -122.320)


def _fetch():
    lat_lo, lat_hi, lon_lo, lon_hi = BBOX
    params = {
        "$where": "latitude > %f AND latitude < %f AND longitude > %f AND longitude < %f "
                  "AND statuscurrent='Completed' AND estprojectcost > 0" % (lat_lo, lat_hi, lon_lo, lon_hi),
        "$select": "permitnum,permittypedesc,description,estprojectcost,issueddate,completeddate,"
                   "originaladdress1,latitude,longitude",
        "$limit": "25000",
    }
    url = API_BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "build-vitals-hackathon/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def load_permits(force_refresh=False):
    if not force_refresh and os.path.exists(CACHE_PATH):
        age = time.time() - os.path.getmtime(CACHE_PATH)
        if age < CACHE_MAX_AGE_S:
            return json.load(open(CACHE_PATH))
    try:
        data = _fetch()
    except Exception as e:
        if os.path.exists(CACHE_PATH):
            return json.load(open(CACHE_PATH))
        raise RuntimeError("could not fetch SDCI permit data and no cache exists: %s" % e)
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    json.dump(data, open(CACHE_PATH, "w"))
    return data


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def near(lat, lon, radius_m=100, permits=None):
    """Real completed permits within radius_m -- deliberately tight radius
    (100m default, not 400m like crime) since permits should belong to
    THIS building, not describe the whole block."""
    if permits is None:
        permits = load_permits()
    hits = []
    for row in permits:
        try:
            rlat, rlon = float(row["latitude"]), float(row["longitude"])
        except (KeyError, ValueError, TypeError):
            continue
        d = _haversine_m(lat, lon, rlat, rlon)
        if d <= radius_m:
            hits.append({**row, "distance_m": round(d, 1)})
    hits.sort(key=lambda h: h["distance_m"])
    total_cost = sum(float(h["estprojectcost"]) for h in hits)
    return {"count": len(hits), "total_cost": total_cost, "permits": hits, "radius_m": radius_m}


def upkeep_score(result):
    """$1.5M+ in completed nearby permit work scores near 100; scales down
    from there. Same style of calibration as the other new scorers --
    illustrative, not a validated investment-adequacy model."""
    if result is None or result["count"] == 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * min(1.0, result["total_cost"] / 1_500_000.0)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--radius-m", type=float, default=100)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    permits = load_permits(force_refresh=a.refresh)
    print("loaded %d cached permits (downtown/SLU bbox, completed+valued)" % len(permits))
    result = near(a.lat, a.lon, a.radius_m, permits)
    print("within %dm: %d permits, $%s total" % (a.radius_m, result["count"], f"{result['total_cost']:,.0f}"))
    for p in result["permits"][:8]:
        print("  $%10s  %-30s %s" % (f"{float(p['estprojectcost']):,.0f}", p["permittypedesc"][:30], p.get("completeddate", "")[:10]))
    print("upkeep_score:", round(upkeep_score(result), 1))
