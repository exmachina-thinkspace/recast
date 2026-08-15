#!/usr/bin/env python3
"""Build (or rebuild) the OSM building footprints embedded in seattle-office-vitals-3d.html.

Pipeline
  1. read the `const B = [...]` building list out of the HTML (fields: i, a, n, la, lo, ...);
  2. fetch every OSM `building` way/relation in one bbox around those points (Overpass API), cached in
     data/osm_raw.json so re-runs are offline;
  3. match each building: point-in-polygon on its coordinate (smallest containing polygon wins), else the
     nearest polygon within --near metres, else a hand-checked OVERRIDES entry, else no footprint (pin only);
  4. write data/footprints.json (matched rings + OSM name/levels/height + how it matched);
  5. buffer each ring --buffer metres outward (miter offset, clamped) so photogrammetry bulge and small
     overhangs still get painted, and embed the lean result as `const F = {...}` in the HTML.

Usage (from the city-view-3d folder):
  python3 tools/build_footprints.py                 # uses cached OSM data if present
  python3 tools/build_footprints.py --refetch       # hit Overpass again
  python3 tools/build_footprints.py --no-embed      # only write data/footprints.json
Requires only the Python standard library.
"""
import argparse, json, math, os, re, sys, time, urllib.parse, urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_HTML = os.path.join(ROOT, "seattle-office-vitals-3d.html")
CACHE = os.path.join(ROOT, "data", "osm_raw.json")
OUT = os.path.join(ROOT, "data", "footprints.json")
ENDPOINTS = ["https://overpass-api.de/api/interpreter",
             "https://overpass.kumi.systems/api/interpreter",
             "https://overpass.private.coffee/api/interpreter"]
# Building # -> OSM id, for points whose geocode misses the building (verified by name/address, Aug 2026).
OVERRIDES = {60: "way/223927905",    # 2101-2121 4th Ave = Fourth and Blanchard Building
             71: "way/363046826",    # 5th & Pine Building
             113: "way/140628855",   # Broderick Building
             123: "way/235386588"}   # 605 Union Station

# ---- planar helpers (metres, good enough across downtown Seattle) ----
K = math.cos(math.radians(47.62)); MX = 111320 * K; MY = 110574
def to_xy(lon, lat): return ((lon + 122.33) * MX, (lat - 47.61) * MY)
def to_ll(x, y): return (x / MX - 122.33, y / MY + 47.61)
def signed_area(r): return sum(r[i][0] * r[(i + 1) % len(r)][1] - r[(i + 1) % len(r)][0] * r[i][1] for i in range(len(r))) / 2
def pip(x, y, ring):
    inside = False; n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]; x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1): inside = not inside
    return inside
def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == dy == 0: return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
def ring_dist(x, y, ring): return min(seg_dist(x, y, *ring[i], *ring[(i + 1) % len(ring)]) for i in range(len(ring)))
def parse_h(tags):
    h = tags.get("height") or tags.get("building:height")
    if not h: return None
    m = re.match(r"\s*([\d.]+)\s*(m|ft|')?", h)
    if not m: return None
    v = float(m.group(1)); return round(v * 0.3048, 1) if m.group(2) in ("ft", "'") else round(v, 1)
def parse_lv(tags):
    try: return int(float(tags["building:levels"])) if tags.get("building:levels") else None
    except ValueError: return None
def dedupe(r):
    out = []
    for p in r:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 0.05: out.append(p)
    if len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= 0.05: out.pop()
    return out
def offset(r, d):
    """Miter-offset a ring (metres, xy) outward by d; miter length clamped to 2d."""
    r = dedupe(r); n = len(r)
    if n < 3: return r
    ccw = signed_area(r) > 0; out = []
    for i in range(n):
        p, c, q = r[i - 1], r[i], r[(i + 1) % n]
        e1 = (c[0] - p[0], c[1] - p[1]); l1 = math.hypot(*e1) or 1; e1 = (e1[0] / l1, e1[1] / l1)
        e2 = (q[0] - c[0], q[1] - c[1]); l2 = math.hypot(*e2) or 1; e2 = (e2[0] / l2, e2[1] / l2)
        n1 = (e1[1], -e1[0]) if ccw else (-e1[1], e1[0]); n2 = (e2[1], -e2[0]) if ccw else (-e2[1], e2[0])
        bx, by = n1[0] + n2[0], n1[1] + n2[1]; bl = math.hypot(bx, by)
        if bl < 1e-6: out.append((c[0] + n1[0] * d, c[1] + n1[1] * d)); continue
        bx, by = bx / bl, by / bl
        cosh = max(0.5, (1 + (n1[0] * n2[0] + n1[1] * n2[1])) / 2) ** 0.5
        out.append((c[0] + bx * d / cosh, c[1] + by * d / cosh))
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", default=DEFAULT_HTML)
    ap.add_argument("--near", type=float, default=15, help="metres: accept nearest polygon if none contains the point")
    ap.add_argument("--buffer", type=float, default=2.0, help="metres to grow each footprint before embedding")
    ap.add_argument("--refetch", action="store_true", help="ignore data/osm_raw.json and query Overpass again")
    ap.add_argument("--no-embed", action="store_true", help="write data/footprints.json only")
    a = ap.parse_args()

    src = open(a.html, encoding="utf-8").read()
    m = re.search(r"const B = (\[.*?\]);\n", src, re.S)
    if not m: sys.exit("could not find `const B = [...];` in the HTML")
    B = json.loads(m.group(1)); print(f"{len(B)} buildings in page")

    data = None
    if not a.refetch and os.path.exists(CACHE):
        data = json.load(open(CACHE)); print(f"using cached {CACHE}")
    else:
        pad = 0.0012
        S, N = min(b["la"] for b in B) - pad, max(b["la"] for b in B) + pad
        W, E = min(b["lo"] for b in B) - pad, max(b["lo"] for b in B) + pad
        q = f'[out:json][timeout:120][bbox:{S},{W},{N},{E}];(way["building"];relation["building"];);out tags geom;'
        for ep in ENDPOINTS:
            try:
                print(f"querying {ep} ...")
                req = urllib.request.Request(ep, data=urllib.parse.urlencode({"data": q}).encode(),
                                             headers={"User-Agent": "seattle-office-vitals-3d/1.0"})
                with urllib.request.urlopen(req, timeout=150) as r: data = json.load(r)
                break
            except Exception as e:  # noqa: BLE001
                print(f"  failed: {e}"); time.sleep(2)
        if data is None: sys.exit("all Overpass endpoints failed")
        os.makedirs(os.path.dirname(CACHE), exist_ok=True); json.dump(data, open(CACHE, "w"))
    print(f"{len(data['elements'])} OSM elements")

    cands = []
    for el in data["elements"]:
        tags = el.get("tags", {})
        rings = []
        if el["type"] == "way" and el.get("geometry") and len(el["geometry"]) >= 4:
            rings.append([(p["lon"], p["lat"]) for p in el["geometry"]])
        elif el["type"] == "relation":
            rings += [[(p["lon"], p["lat"]) for p in mem["geometry"]] for mem in el.get("members", [])
                      if mem.get("role") == "outer" and mem.get("geometry")]
        for ring in rings:
            if ring and ring[0] == ring[-1]: ring = ring[:-1]
            if len(ring) >= 3: cands.append((f"{el['type']}/{el['id']}", tags, ring, [to_xy(*p) for p in ring]))
    print(f"{len(cands)} candidate rings")

    out, missing = {}, []
    for b in B:
        x, y = to_xy(b["lo"], b["la"]); pick = None
        if b["i"] in OVERRIDES:
            pick = next(((c, t, r) for c, t, r, _ in cands if c == OVERRIDES[b["i"]]), None); how = "override"
        if not pick:
            inside = sorted(((abs(signed_area(rxy)), c, t, r) for c, t, r, rxy in cands if pip(x, y, rxy)), key=lambda t: t[0])
            if inside: _, c, t, r = inside[0]; pick = (c, t, r); how = "inside"
        if not pick:
            near = min(((ring_dist(x, y, rxy), c, t, r) for c, t, r, rxy in cands), key=lambda t: t[0], default=None)
            if near and near[0] <= a.near: d, c, t, r = near; pick = (c, t, r); how = f"near:{d:.0f}"
        if not pick: missing.append(b); continue
        c, t, r = pick
        out[str(b["i"])] = {"p": [[round(lon, 6), round(lat, 6)] for lon, lat in r], "h": parse_h(t), "lv": parse_lv(t),
                            "osm": c, "nm": t.get("name"), "how": how}
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"matched {len(out)}/{len(B)} -> {OUT}")
    for b in missing: print(f"  no footprint: #{b['i']} {b['a']} ({b['n']})")
    for k, cnt in Counter(v["osm"] for v in out.values()).items():
        if cnt > 1: print(f"  shared footprint {k}: #{', #'.join(i for i, v in out.items() if v['osm'] == k)}")

    if a.no_embed: return
    lean = {}
    for k, v in out.items():
        ring = [to_ll(*p) for p in offset([to_xy(*p) for p in v["p"]], a.buffer)]
        o = {"p": [[round(lon, 6), round(lat, 6)] for lon, lat in ring]}
        for f in ("nm", "lv", "h"):
            if v.get(f): o[f] = v[f]
        o["osm"] = v["osm"]; lean[k] = o
    m2 = re.search(r"const F = (\{.*?\});\n", src, re.S)
    if not m2: sys.exit("could not find `const F = {...};` in the HTML")
    src = src[:m2.start(1)] + json.dumps(lean, separators=(",", ":")) + src[m2.end(1):]
    open(a.html, "w", encoding="utf-8").write(src)
    print(f"embedded {len(lean)} footprints (buffered {a.buffer} m) into {a.html}")

if __name__ == "__main__":
    main()
