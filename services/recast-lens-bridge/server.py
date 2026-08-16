"""Recast Lens frame bridge.

Receives browser-camera JPEG frames from the Recast frontend and keeps the
latest frame available for downstream VSS/Recast adapters.

This is intentionally small and dependency-free:

  python3 server.py --port 8910
"""

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(__file__), "runtime")
MAX_FRAME_BYTES = 6 * 1024 * 1024


class State:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.latest_frame_path = os.path.join(data_dir, "latest.jpg")
        self.latest_meta_path = os.path.join(data_dir, "latest.json")
        self.frame_count = 0
        self.latest_meta = None
        os.makedirs(data_dir, exist_ok=True)

    def write_frame(self, frame_bytes, headers):
        self.frame_count += 1
        now = time.time()
        meta = {
            "ok": True,
            "frame_count": self.frame_count,
            "received_at": now,
            "received_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "bytes": len(frame_bytes),
            "session_id": headers.get("X-Recast-Session") or None,
            "device_label": headers.get("X-Recast-Device") or None,
            "source": headers.get("X-Recast-Source") or None,
        }
        tmp = self.latest_frame_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(frame_bytes)
        os.replace(tmp, self.latest_frame_path)
        with open(self.latest_meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        self.latest_meta = meta
        return meta

    def status(self):
        if self.latest_meta is None and os.path.exists(self.latest_meta_path):
            try:
                with open(self.latest_meta_path, encoding="utf-8") as f:
                    self.latest_meta = json.load(f)
            except json.JSONDecodeError:
                self.latest_meta = None
        return {
            "ok": True,
            "frame_count": self.frame_count,
            "has_frame": os.path.exists(self.latest_frame_path),
            "latest": self.latest_meta,
        }


class Handler(BaseHTTPRequestHandler):
    state = None

    def _send_html(self, status, body):
        payload = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Recast-Session, X-Recast-Device, X-Recast-Source")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, status, path, content_type):
        try:
            with open(path, "rb") as f:
                payload = f.read()
        except FileNotFoundError:
            self._send_json(404, {"error": "no frame received yet"})
            return
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self._send_json(204, {})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(200, {"ok": True, "service": "recast-lens-bridge"})
            return
        if path in {"/", "/viewer", "/api/recast-lens/viewer"}:
            self._send_html(200, VIEWER_HTML)
            return
        if path == "/api/recast-lens/status":
            self._send_json(200, self.state.status())
            return
        if path == "/api/recast-lens/latest.jpg":
            self._send_file(200, self.state.latest_frame_path, "image/jpeg")
            return
        self._send_json(404, {"error": "not found"})

    def do_HEAD(self):
        path = urlparse(self.path).path
        if path in {"/", "/viewer", "/api/recast-lens/viewer"}:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return
        if path == "/api/recast-lens/latest.jpg":
            if not os.path.exists(self.state.latest_frame_path):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            return
        if path in {"/health", "/api/recast-lens/status"}:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/recast-lens/frame":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid content length"})
            return
        if length <= 0:
            self._send_json(400, {"error": "empty frame"})
            return
        if length > MAX_FRAME_BYTES:
            self._send_json(413, {"error": "frame too large"})
            return
        frame_bytes = self.rfile.read(length)
        if not frame_bytes.startswith(b"\xff\xd8"):
            self._send_json(415, {"error": "expected jpeg frame"})
            return
        meta = self.state.write_frame(frame_bytes, self.headers)
        self._send_json(201, meta)

    def log_message(self, fmt, *args):
        sys.stderr.write("[recast-lens-bridge] " + (fmt % args) + "\n")


VIEWER_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Recast Lens Viewer</title>
    <style>
      :root { color-scheme: dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
      body { margin: 0; min-height: 100vh; background: #070a0f; color: #f5f7fb; display: grid; grid-template-rows: auto 1fr auto; }
      header, footer { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 12px 16px; background: #101722; border-bottom: 1px solid #263244; }
      footer { border-top: 1px solid #263244; border-bottom: 0; color: #9aa6b8; font-size: 13px; }
      h1 { margin: 0; font-size: 16px; letter-spacing: 0; }
      .pill { padding: 5px 8px; border-radius: 5px; background: #193b2b; color: #4ee18b; font-size: 12px; font-weight: 800; }
      main { min-height: 0; display: grid; place-items: center; padding: 16px; }
      img { max-width: 100%; max-height: calc(100vh - 112px); object-fit: contain; border: 1px solid #263244; background: #000; }
      #meta { overflow-wrap: anywhere; }
    </style>
  </head>
  <body>
    <header><h1>Recast Lens Viewer</h1><span class="pill" id="state">waiting</span></header>
    <main><img id="frame" alt="latest Recast Lens frame" /></main>
    <footer><span id="meta">No frame yet.</span><span>refresh 2 fps</span></footer>
    <script>
      async function tick() {
        const ts = Date.now();
        const img = document.getElementById('frame');
        const state = document.getElementById('state');
        const meta = document.getElementById('meta');
        try {
          const res = await fetch('/api/recast-lens/status?ts=' + ts, { cache: 'no-store' });
          const data = await res.json();
          if (data.has_frame) {
            img.src = '/api/recast-lens/latest.jpg?ts=' + ts;
            state.textContent = 'live';
            const latest = data.latest || {};
            meta.textContent = `frame ${latest.frame_count || data.frame_count || '?'} · ${latest.bytes || '?'} bytes · ${latest.received_at_iso || 'unknown time'} · ${latest.session_id || 'no session'}`;
          } else {
            state.textContent = 'waiting';
            meta.textContent = 'No frame received yet.';
          }
        } catch (e) {
          state.textContent = 'offline';
          meta.textContent = e.message;
        }
      }
      tick();
      setInterval(tick, 500);
    </script>
  </body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8910)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    args = parser.parse_args()

    Handler.state = State(args.data_dir)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"[recast-lens-bridge] listening on http://0.0.0.0:{args.port}")
    print(f"[recast-lens-bridge] latest frame: {Handler.state.latest_frame_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
