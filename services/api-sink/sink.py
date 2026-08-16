"""Minimal local stand-in for Michael's services/api, scoped ONLY to
receiving and validating sensor_observation events so the vision-bridge
path is testable end-to-end before the real API exists.

Not a real backend: no database, no auth, no building spine lookup, no
score computation. Stdlib http.server only, so it needs zero extra
installs on the Spark box.

  POST /observations   -> validate + append to observations.jsonl
  GET  /observations    -> return all logged events as a JSON array
  GET  /health          -> {"ok": true}

Usage:
  python3 sink.py --port 8600
"""
import os
import sys
import json
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vision-bridge"))
from contract import validate, ContractError

LOG_PATH = os.path.join(os.path.dirname(__file__), "observations.jsonl")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
        elif self.path == "/observations":
            events = []
            if os.path.exists(LOG_PATH):
                with open(LOG_PATH) as f:
                    events = [json.loads(line) for line in f if line.strip()]
            self._send(200, {"count": len(events), "events": events})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/observations":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            event = json.loads(raw)
            validate(event)
        except (json.JSONDecodeError, ContractError) as e:
            self._send(400, {"error": str(e)})
            return
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(event) + "\n")
        self._send(201, {"accepted": event["event_id"]})

    def log_message(self, fmt, *args):
        sys.stderr.write("[sink] " + (fmt % args) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8600)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[sink] listening on http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
