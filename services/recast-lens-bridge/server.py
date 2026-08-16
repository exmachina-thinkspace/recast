"""Recast Lens frame bridge.

Receives browser-camera JPEG frames from the Recast frontend and keeps the
latest frame available for downstream VSS/Recast adapters.

This is intentionally small and dependency-free:

  python3 server.py --port 8910
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(__file__), "runtime")
MAX_FRAME_BYTES = 6 * 1024 * 1024
DEFAULT_COSMOS_API = os.environ.get("RECAST_COSMOS_API", "http://127.0.0.1:30082/v1/chat/completions")
DEFAULT_COSMOS_MODEL = os.environ.get("RECAST_COSMOS_MODEL", "nvidia/cosmos3-nano-reasoner")


class State:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.latest_frame_path = os.path.join(data_dir, "latest.jpg")
        self.latest_meta_path = os.path.join(data_dir, "latest.json")
        self.latest_interpretation_path = os.path.join(data_dir, "latest-interpretation.json")
        self.frame_count = 0
        self.latest_meta = None
        self.latest_interpretation = None
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

    def interpretation_status(self):
        if self.latest_interpretation is None and os.path.exists(self.latest_interpretation_path):
            try:
                with open(self.latest_interpretation_path, encoding="utf-8") as f:
                    self.latest_interpretation = json.load(f)
            except json.JSONDecodeError:
                self.latest_interpretation = None
        return {"ok": True, "latest_interpretation": self.latest_interpretation}

    def interpret_latest(self, question=None):
        if not os.path.exists(self.latest_frame_path):
            return {"error": "no frame received yet"}

        with open(self.latest_frame_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()

        prompt = question or (
            "You are interpreting a live iPhone walkthrough frame for Recast. "
            "In 2-3 concise sentences, describe what is visible. Mention layout, "
            "condition, people/activity, and any obvious evidence useful for "
            "building reuse. Do not guess the building name or make claims beyond "
            "the visible frame."
        )
        payload = json.dumps({
            "model": DEFAULT_COSMOS_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]}],
            "max_tokens": 240,
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request(DEFAULT_COSMOS_API, data=payload, headers={"Content-Type": "application/json"})
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=75) as r:
                resp = json.load(r)
            description = resp["choices"][0]["message"].get("content") or ""
        except Exception as exc:
            return {"error": f"vision interpretation failed: {exc}"}

        result = {
            "ok": True,
            "source": "latest Recast Lens frame",
            "engine": "local NVIDIA Cosmos/VSS-side vision reasoner",
            "model": DEFAULT_COSMOS_MODEL,
            "description": description.strip(),
            "question": question,
            "frame": self.latest_meta,
            "elapsed_s": round(time.time() - started, 2),
            "created_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with open(self.latest_interpretation_path, "w", encoding="utf-8") as f:
            json.dump(result, f)
        self.latest_interpretation = result
        return result

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
            "interpretation": self.interpretation_status()["latest_interpretation"],
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
        if path == "/api/recast-lens/interpretation":
            self._send_json(200, self.state.interpretation_status())
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
        if path in {"/health", "/api/recast-lens/status", "/api/recast-lens/interpretation"}:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/recast-lens/interpret":
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            result = self.state.interpret_latest(body.get("question"))
            self._send_json(500 if result.get("error") else 200, result)
            return
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
      body { margin: 0; min-height: 100vh; background: #070a0f; color: #f5f7fb; display: grid; grid-template-rows: auto 1fr auto auto; }
      header, footer { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 12px 16px; background: #101722; border-bottom: 1px solid #263244; }
      footer { border-top: 1px solid #263244; border-bottom: 0; color: #9aa6b8; font-size: 13px; }
      h1 { margin: 0; font-size: 16px; letter-spacing: 0; }
      .pill { padding: 5px 8px; border-radius: 5px; background: #193b2b; color: #4ee18b; font-size: 12px; font-weight: 800; }
      main { min-height: 0; display: grid; place-items: center; padding: 16px; }
      img { max-width: 100%; max-height: calc(100vh - 190px); object-fit: contain; border: 1px solid #263244; background: #000; }
      #meta { overflow-wrap: anywhere; }
      #answer { padding: 12px 16px; background: #111b28; border-top: 1px solid #263244; font-size: 15px; line-height: 1.45; }
      button { border: 0; border-radius: 5px; padding: 8px 10px; background: #2f6fed; color: #fff; font-weight: 800; cursor: pointer; }
      button:disabled { opacity: .6; cursor: not-allowed; }
    </style>
  </head>
  <body>
    <header><h1>Recast Lens Viewer</h1><span class="pill" id="state">waiting</span></header>
    <main><img id="frame" alt="latest Recast Lens frame" /></main>
    <footer><span id="meta">No frame yet.</span><button id="interpret" type="button">What am I seeing?</button></footer>
    <div id="answer">No interpretation yet.</div>
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
      async function interpret() {
        const button = document.getElementById('interpret');
        const answer = document.getElementById('answer');
        button.disabled = true;
        button.textContent = 'Thinking...';
        answer.textContent = 'Asking the local NVIDIA vision reasoner...';
        try {
          const res = await fetch('/api/recast-lens/interpret', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: 'What am I seeing in this Recast Lens frame?' })
          });
          const data = await res.json();
          answer.textContent = data.description || data.error || 'No interpretation returned.';
        } catch (e) {
          answer.textContent = e.message;
        } finally {
          button.disabled = false;
          button.textContent = 'What am I seeing?';
        }
      }
      document.getElementById('interpret').addEventListener('click', interpret);
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
