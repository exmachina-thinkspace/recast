"""Real crime-incident search near a building, using Seattle Police
Department's public open dataset (SPD Crime Data: 2008-Present,
data.seattle.gov, Socrata dataset tazs-3rd5). No API key required for
read-only public queries at this volume.

Fetches once per cache window (default 24h) rather than per-building --
West precinct alone (downtown/SLU/Queen Anne) runs ~22k incidents/year, so
this pulls that batch once and does proximity filtering locally for every
building, instead of one API call per building.

Some SPD rows have block_address/latitude/longitude redacted (privacy
protection on sensitive offense categories, e.g. sexual assault, domestic
violence) -- these are skipped, not treated as "no crime here". Evidence
tier: T2, official police record, real and current, but this dataset
undercounts by design for those categories.

Usage:
  python3 crime_search.py --lat 47.6339 --lon -122.3396 --radius-m 400
"""
import os
import json
import math
import time
import argparse
import urllib.request
import urllib.parse

CACHE_PATH = os.path.expanduser("~/plans/city-view-3d/data/spd_crime_west.json")
CACHE_MAX_AGE_S = 24 * 3600
API_BASE = "https://data.seattle.gov/resource/tazs-3rd5.json"


def _fetch_precinct(precinct="West", months_back=12):
    since = time.strftime("%Y-%m-%dT00:00:00", time.gmtime(time.time() - months_back * 30 * 86400))
    params = {
        "$where": "offense_date > '%s' AND precinct='%s'" % (since, precinct),
        "$select": "offense_category,nibrs_offense_code_description,offense_date,block_address,latitude,longitude,neighborhood",
        "$limit": "25000",
    }
    url = API_BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "build-vitals-hackathon/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def load_incidents(force_refresh=False):
    if not force_refresh and os.path.exists(CACHE_PATH):
        age = time.time() - os.path.getmtime(CACHE_PATH)
        if age < CACHE_MAX_AGE_S:
            return json.load(open(CACHE_PATH))
    try:
        data = _fetch_precinct()
    except Exception as e:
        # Fall back to stale cache rather than failing the whole scorer
        if os.path.exists(CACHE_PATH):
            return json.load(open(CACHE_PATH))
        raise RuntimeError("could not fetch SPD crime data and no cache exists: %s" % e)
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


def near(lat, lon, radius_m=400, incidents=None):
    """Real incidents within radius_m of (lat, lon). Skips rows with
    redacted or unparseable coordinates -- does not count them as zero,
    just excludes them (they're real crimes with hidden location, not
    absence of crime)."""
    if incidents is None:
        incidents = load_incidents()
    hits = []
    skipped_redacted = 0
    for row in incidents:
        try:
            rlat, rlon = float(row["latitude"]), float(row["longitude"])
        except (KeyError, ValueError, TypeError):
            skipped_redacted += 1
            continue
        d = _haversine_m(lat, lon, rlat, rlon)
        if d <= radius_m:
            hits.append({**row, "distance_m": round(d, 1)})
    hits.sort(key=lambda h: h["distance_m"])
    by_category = {}
    for h in hits:
        c = h.get("offense_category", "UNKNOWN")
        by_category[c] = by_category.get(c, 0) + 1
    return {"count": len(hits), "by_category": by_category, "incidents": hits,
            "skipped_redacted_citywide": skipped_redacted, "radius_m": radius_m}


def safety_score(result):
    """Deterministic score from incident density, weighted by severity and
    log-dampened. A flat linear penalty zeroed out every dense downtown
    location tested (200-700+ incidents/year within 400m is normal for
    Seattle's urban core, mostly routine property crime -- not itself
    evidence of an unsafe building), so this compresses the range with
    log(1+penalty) instead, and still weights violent crime far more per
    incident than routine property crime. Thresholds are a first-pass
    calibration for relative comparison between buildings, NOT a
    validated crime-risk model or a claim about absolute safety -- note
    this honestly wherever the score is shown."""
    by_cat = result["by_category"]
    violent = by_cat.get("VIOLENT CRIME", 0)
    property_ = by_cat.get("PROPERTY CRIME", 0)
    other = sum(v for k, v in by_cat.items() if k not in ("VIOLENT CRIME", "PROPERTY CRIME"))
    penalty = violent * 3.0 + property_ * 0.15 + other * 0.1
    return max(0.0, min(100.0, 100.0 - 15.0 * math.log1p(penalty)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--radius-m", type=float, default=400)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    incidents = load_incidents(force_refresh=a.refresh)
    print("loaded %d cached incidents (West precinct, last ~12mo)" % len(incidents))
    result = near(a.lat, a.lon, a.radius_m, incidents)
    print("within %dm: %d incidents (%d citywide redacted/unmapped, excluded not zeroed)" %
          (a.radius_m, result["count"], result["skipped_redacted_citywide"]))
    for cat, n in sorted(result["by_category"].items(), key=lambda kv: -kv[1]):
        print("  %-20s %d" % (cat, n))
    print("safety_score:", round(safety_score(result), 1))
