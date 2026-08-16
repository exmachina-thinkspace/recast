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
        if path == "/api/recast-lens/status":
            self._send_json(200, self.state.status())
            return
        if path == "/api/recast-lens/latest.jpg":
            self._send_file(200, self.state.latest_frame_path, "image/jpeg")
            return
        self._send_json(404, {"error": "not found"})

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
