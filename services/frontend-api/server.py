"""Read-only JSON API for the combined Recast frontend.

New, additive service on its own port -- does not modify or replace
city-view-3d's embedded data, generate_city_bhi.py, or the voice-agent
server. Reads the same real data those already produce (the city-view
HTML's inline B/BV, and ~/plans/build_vitals_v2.json for the hero's full
evidence) and serves it as plain JSON so a separate frontend app (running
on a different port, possibly built with a framework) can consume it
without needing to regex-scrape an HTML file itself.

Endpoints:
  GET /api/buildings          -> [{i, name, address, la, lo, bhi, evidence_coverage}, ...]
  GET /api/buildings/<i>       -> full building + BV record (all vitals/inputs)
  GET /api/buildings/hero/floorplan       -> real measured floor-plan metadata + image URLs
  GET /api/buildings/hero/floorplan/<lvl>.png -> the actual floor-plan PNG (lvl = level1|level2)
  GET /health

Usage:
  python3 server.py --port 8900
"""
import os
import re
import sys
import json
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PLANS = os.path.expanduser("~/plans")
CITY_VIEW_HTML = os.path.join(PLANS, "city-view-3d", "seattle-office-vitals-3d.html")
FLOORPLAN_META = os.path.join(PLANS, "floorplan_meta.json")
FLOORPLAN_PNG = {"level1": os.path.join(PLANS, "level1_floorplan.png"),
                  "level2": os.path.join(PLANS, "level2_floorplan.png")}


def load_city_data():
    src = open(CITY_VIEW_HTML, encoding="utf-8").read()
    mB = re.search(r"const B = (\[.*?\]);\n", src, re.S)
    mBV = re.search(r"const BV = (\{.*?\});\n", src, re.S)
    B = json.loads(mB.group(1))
    BV = json.loads(mBV.group(1))
    return B, BV


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _send_png(self, status, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            self._send_json(404, {"error": "floor plan image not found"})
            return
        self.send_response(status)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"ok": True})
            return

        if self.path == "/api/buildings":
            try:
                B, BV = load_city_data()
            except Exception as e:
                self._send_json(500, {"error": "could not load city data: %s" % e})
                return
            out = []
            for b in B:
                bv = BV.get(str(b["i"]))
                out.append({
                    "i": b["i"], "name": b.get("n") or b["a"], "address": b["a"],
                    "la": b.get("la"), "lo": b.get("lo"),
                    "bhi": bv["bhi"] if bv else None,
                    "evidence_coverage": bv["evidence_coverage"] if bv else 0.0,
                    "has_score": bv is not None,
                })
            self._send_json(200, out)
            return

        if self.path == "/api/buildings/hero/floorplan":
            try:
                meta = json.load(open(FLOORPLAN_META, encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as e:
                self._send_json(500, {"error": "could not load floorplan_meta.json: %s" % e})
                return
            levels = []
            for lvl, info in meta.items():
                if lvl not in FLOORPLAN_PNG or not os.path.exists(FLOORPLAN_PNG[lvl]):
                    continue
                levels.append({
                    "level": lvl,
                    "image_url": "/api/buildings/hero/floorplan/%s.png" % lvl,
                    "width_px": info.get("width_px"),
                    "height_px": info.get("height_px"),
                    "extent_ft": info.get("extent_ft"),
                    "source": "extract_plan.py / plan2model.py, real measured architectural plan",
                })
            self._send_json(200, {"building": "Lake Union Building, 1700 Westlake Ave N", "levels": levels})
            return

        m2 = re.match(r"^/api/buildings/hero/floorplan/(level[12])\.png$", self.path)
        if m2:
            self._send_png(200, FLOORPLAN_PNG[m2.group(1)])
            return

        m = re.match(r"^/api/buildings/(\d+)$", self.path)
        if m:
            idx = m.group(1)
            try:
                B, BV = load_city_data()
            except Exception as e:
                self._send_json(500, {"error": "could not load city data: %s" % e})
                return
            b = next((x for x in B if str(x["i"]) == idx), None)
            if not b:
                self._send_json(404, {"error": "no building #%s" % idx})
                return
            bv = BV.get(idx)
            self._send_json(200, {"building": b, "bhi_record": bv})
            return

        self._send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[frontend-api] " + (fmt % args) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8900)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("[frontend-api] listening on http://0.0.0.0:%d" % args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
