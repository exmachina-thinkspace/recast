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
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(__file__), "runtime")
MAX_FRAME_BYTES = 6 * 1024 * 1024
LIVE_FRAME_MAX_AGE_S = 5
VIEWER_REFRESH_INTERVAL_MS = 200
OBJECT_DETECT_INTERVAL_MS = 5000
OBJECT_DETECT_MIN_INTERVAL_S = 4
DEFAULT_COSMOS_API = os.environ.get("RECAST_COSMOS_API", "http://127.0.0.1:30082/v1/chat/completions")
DEFAULT_COSMOS_MODEL = os.environ.get("RECAST_COSMOS_MODEL", "nvidia/cosmos3-nano-reasoner")
DEFAULT_DETECTOR_PYTHON = os.environ.get("RECAST_DETECTOR_PYTHON", "/home/acer01/arlo-vision/bin/python")
DEFAULT_DETECTOR_SCRIPT = os.environ.get("RECAST_DETECTOR_SCRIPT", os.path.join(os.path.dirname(__file__), "detect_objects.py"))
DEFAULT_YOLO_MODEL = os.environ.get("RECAST_YOLO_MODEL", "/home/acer01/arlo-vision/yolo11m.pt")
SESSION_ID_MAX_LEN = 96


def sanitize_session_id(value):
    if not value:
        return None
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(value))
    cleaned = cleaned.strip(".-_")
    return cleaned[:SESSION_ID_MAX_LEN] or None


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


class State:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.sessions_dir = os.path.join(data_dir, "sessions")
        self.latest_frame_path = os.path.join(data_dir, "latest.jpg")
        self.latest_meta_path = os.path.join(data_dir, "latest.json")
        self.latest_interpretation_path = os.path.join(data_dir, "latest-interpretation.json")
        self.latest_objects_path = os.path.join(data_dir, "latest-objects.json")
        self.frame_count = 0
        self.latest_meta = None
        self.latest_interpretation = None
        self.latest_objects = None
        self.detect_lock = threading.Lock()
        self.tracking_enabled = False
        self.tracking_sessions = set()
        self.tracking_thread = None
        self.tracking_stop = threading.Event()
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(self.sessions_dir, exist_ok=True)

    def session_paths(self, session_id):
        session_id = sanitize_session_id(session_id)
        if not session_id:
            return {
                "session_id": None,
                "frame": self.latest_frame_path,
                "meta": self.latest_meta_path,
                "interpretation": self.latest_interpretation_path,
                "objects": self.latest_objects_path,
            }
        session_dir = os.path.join(self.sessions_dir, session_id)
        os.makedirs(session_dir, exist_ok=True)
        return {
            "session_id": session_id,
            "frame": os.path.join(session_dir, "latest.jpg"),
            "meta": os.path.join(session_dir, "latest.json"),
            "interpretation": os.path.join(session_dir, "latest-interpretation.json"),
            "objects": os.path.join(session_dir, "latest-objects.json"),
        }

    def session_ids(self):
        try:
            names = os.listdir(self.sessions_dir)
        except FileNotFoundError:
            return []
        return sorted(name for name in names if sanitize_session_id(name) == name and os.path.isdir(os.path.join(self.sessions_dir, name)))

    def sessions_status(self):
        return [self.status(session_id) for session_id in self.session_ids()]

    def frame_status(self, session_id=None):
        paths = self.session_paths(session_id)
        latest_meta = read_json(paths["meta"])
        if paths["session_id"] is None:
            self.latest_meta = latest_meta
        frame_age_s = None
        if latest_meta and latest_meta.get("received_at"):
            frame_age_s = round(time.time() - float(latest_meta["received_at"]), 2)
        return {
            "has_frame": os.path.exists(paths["frame"]),
            "frame_age_s": frame_age_s,
            "is_live": frame_age_s is not None and frame_age_s <= LIVE_FRAME_MAX_AGE_S,
            "latest": latest_meta,
        }

    def write_frame(self, frame_bytes, headers):
        self.frame_count += 1
        session_id = sanitize_session_id(headers.get("X-Recast-Session"))
        now = time.time()
        meta = {
            "ok": True,
            "frame_count": self.frame_count,
            "received_at": now,
            "received_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "bytes": len(frame_bytes),
            "session_id": session_id,
            "device_label": headers.get("X-Recast-Device") or None,
            "source": headers.get("X-Recast-Source") or None,
        }
        for paths in (self.session_paths(session_id), self.session_paths(None)):
            tmp = paths["frame"] + ".tmp"
            with open(tmp, "wb") as f:
                f.write(frame_bytes)
            os.replace(tmp, paths["frame"])
            with open(paths["meta"], "w", encoding="utf-8") as f:
                json.dump(meta, f)
        self.latest_meta = meta
        return meta

    def interpretation_status(self, session_id=None):
        paths = self.session_paths(session_id)
        latest_interpretation = read_json(paths["interpretation"])
        if paths["session_id"] is None:
            self.latest_interpretation = latest_interpretation
        return {"ok": True, "latest_interpretation": latest_interpretation}

    def interpret_latest(self, question=None, session_id=None):
        paths = self.session_paths(session_id)
        if not os.path.exists(paths["frame"]):
            return {"error": "no frame received yet"}

        with open(paths["frame"], "rb") as f:
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
            "frame": read_json(paths["meta"]),
            "session_id": paths["session_id"],
            "elapsed_s": round(time.time() - started, 2),
            "created_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with open(paths["interpretation"], "w", encoding="utf-8") as f:
            json.dump(result, f)
        if paths["session_id"] is None:
            self.latest_interpretation = result
        return result

    def object_status(self, session_id=None):
        paths = self.session_paths(session_id)
        latest_objects = read_json(paths["objects"])
        if paths["session_id"] is None:
            self.latest_objects = latest_objects
        return {"ok": True, "latest_objects": latest_objects}

    def detect_objects(self, session_id=None):
        paths = self.session_paths(session_id)
        if not os.path.exists(paths["frame"]):
            return {"error": "no frame received yet"}

        cached = self.object_status(session_id)["latest_objects"]
        if cached and cached.get("created_at_epoch"):
            age = time.time() - float(cached["created_at_epoch"])
            if age < OBJECT_DETECT_MIN_INTERVAL_S:
                cached["cached"] = True
                cached["cache_age_s"] = round(age, 2)
                return cached

        if not self.detect_lock.acquire(blocking=False):
            cached = self.object_status(session_id)["latest_objects"]
            if cached:
                cached["cached"] = True
                cached["in_flight"] = True
                return cached
            return {"error": "object detection already running"}

        started = time.time()
        try:
            cmd = [
                DEFAULT_DETECTOR_PYTHON,
                DEFAULT_DETECTOR_SCRIPT,
                paths["frame"],
                "--model",
                DEFAULT_YOLO_MODEL,
            ]
            try:
                proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=45)
            except Exception as exc:
                return {"error": f"object detection failed: {exc}"}
            if proc.returncode != 0:
                detail = (proc.stdout or proc.stderr or "").strip()
                return {"error": f"object detection failed: {detail or proc.returncode}"}
            try:
                result = json.loads(proc.stdout)
            except json.JSONDecodeError:
                return {"error": "object detection returned invalid JSON"}
            if result.get("error"):
                return result
            now = time.time()
            result.update({
                "source": "latest Recast Lens frame",
                "frame": read_json(paths["meta"]),
                "session_id": paths["session_id"],
                "elapsed_s": round(now - started, 2),
                "created_at_epoch": now,
                "created_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            })
            with open(paths["objects"], "w", encoding="utf-8") as f:
                json.dump(result, f)
            if paths["session_id"] is None:
                self.latest_objects = result
            return result
        finally:
            self.detect_lock.release()

    def tracking_status(self, session_id=None):
        session_id = sanitize_session_id(session_id)
        running = self.tracking_thread is not None and self.tracking_thread.is_alive()
        return {
            "enabled": session_id in self.tracking_sessions if session_id else self.tracking_enabled,
            "running": running,
            "interval_ms": OBJECT_DETECT_INTERVAL_MS,
            "min_interval_s": OBJECT_DETECT_MIN_INTERVAL_S,
        }

    def set_tracking(self, enabled, session_id=None):
        session_id = sanitize_session_id(session_id)
        if enabled:
            if session_id:
                self.tracking_sessions.add(session_id)
            else:
                self.tracking_enabled = True
            if self.tracking_thread is None or not self.tracking_thread.is_alive():
                self.tracking_stop.clear()
                self.tracking_thread = threading.Thread(target=self._tracking_loop, daemon=True)
                self.tracking_thread.start()
        else:
            if session_id:
                self.tracking_sessions.discard(session_id)
            else:
                self.tracking_enabled = False
            if not self.tracking_enabled and not self.tracking_sessions:
                self.tracking_stop.set()
        return {"ok": True, "tracking": self.tracking_status(session_id)}

    def _tracking_loop(self):
        while not self.tracking_stop.is_set():
            targets = list(self.tracking_sessions)
            if self.tracking_enabled:
                targets.append(None)
            if not targets:
                self.tracking_stop.wait(OBJECT_DETECT_INTERVAL_MS / 1000.0)
                continue
            for session_id in targets:
                if self.frame_status(session_id)["is_live"]:
                    self.detect_objects(session_id)
            self.tracking_stop.wait(OBJECT_DETECT_INTERVAL_MS / 1000.0)

    def status(self, session_id=None):
        session_id = sanitize_session_id(session_id)
        frame = self.frame_status(session_id)
        return {
            "ok": True,
            "session_id": session_id,
            "frame_count": self.frame_count,
            **frame,
            "interpretation": self.interpretation_status(session_id)["latest_interpretation"],
            "objects": self.object_status(session_id)["latest_objects"],
            "tracking": self.tracking_status(session_id),
            "sessions": self.session_ids() if session_id is None else None,
        }


class Handler(BaseHTTPRequestHandler):
    state = None

    def _query(self):
        return parse_qs(urlparse(self.path).query)

    def _session_from_query(self):
        return sanitize_session_id((self._query().get("session") or [None])[0])

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def _send_html(self, status, body):
        payload = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
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
        self.send_header("Cache-Control", "no-store")
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
        if path == "/api/recast-lens/sessions":
            self._send_json(200, {"ok": True, "sessions": self.state.sessions_status()})
            return
        if path == "/api/recast-lens/status":
            self._send_json(200, self.state.status(self._session_from_query()))
            return
        if path == "/api/recast-lens/interpretation":
            self._send_json(200, self.state.interpretation_status(self._session_from_query()))
            return
        if path == "/api/recast-lens/objects":
            self._send_json(200, self.state.object_status(self._session_from_query()))
            return
        if path == "/api/recast-lens/tracking":
            self._send_json(200, {"ok": True, "tracking": self.state.tracking_status(self._session_from_query())})
            return
        if path == "/api/recast-lens/latest.jpg":
            self._send_file(200, self.state.session_paths(self._session_from_query())["frame"], "image/jpeg")
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
            if not os.path.exists(self.state.session_paths(self._session_from_query())["frame"]):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            return
        if path in {"/health", "/api/recast-lens/status", "/api/recast-lens/interpretation", "/api/recast-lens/objects", "/api/recast-lens/tracking"}:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/recast-lens/interpret":
            body = self._read_json_body()
            result = self.state.interpret_latest(body.get("question"), body.get("session") or self._session_from_query())
            self._send_json(500 if result.get("error") else 200, result)
            return
        if path == "/api/recast-lens/detect-objects":
            session_id = self._session_from_query()
            if not session_id and self.headers.get("Content-Type", "").startswith("application/json"):
                session_id = self._read_json_body().get("session")
            result = self.state.detect_objects(session_id)
            self._send_json(500 if result.get("error") else 200, result)
            return
        if path == "/api/recast-lens/tracking":
            body = self._read_json_body()
            result = self.state.set_tracking(bool(body.get("enabled")), body.get("session") or self._session_from_query())
            self._send_json(200, result)
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
      .pill.stale { background: #3a2b16; color: #f3b955; }
      .pill.offline { background: #3b1919; color: #ff8a7d; }
      main { min-height: 0; display: grid; place-items: center; padding: 16px; }
      #frameShell { position: relative; display: inline-block; max-width: 100%; }
      img { display: block; max-width: 100%; max-height: calc(100vh - 190px); object-fit: contain; border: 1px solid #263244; background: #000; }
      #boxLayer { position: absolute; inset: 1px; pointer-events: none; }
      .object-box { position: absolute; border: 2px solid #4ee18b; box-shadow: 0 0 0 1px rgba(7, 10, 15, .72), 0 0 18px rgba(78, 225, 139, .22); }
      .object-box span { position: absolute; left: -2px; top: -25px; max-width: 160px; padding: 4px 6px; border-radius: 4px 4px 4px 0; background: rgba(7, 10, 15, .86); color: #f5f7fb; font-size: 10.5px; font-weight: 900; line-height: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      #meta { overflow-wrap: anywhere; }
      #aiMeta { color: #c8d0dc; font-size: 12px; overflow-wrap: anywhere; }
      #answer { padding: 12px 16px; background: #111b28; border-top: 1px solid #263244; font-size: 15px; line-height: 1.45; }
      button { border: 0; border-radius: 5px; padding: 8px 10px; background: #2f6fed; color: #fff; font-weight: 800; cursor: pointer; }
      button.ghost { background: #263244; }
      button:disabled { opacity: .6; cursor: not-allowed; }
    </style>
  </head>
  <body>
    <header><h1>Recast Lens Viewer</h1><span class="pill" id="state">waiting</span></header>
    <main>
      <div id="frameShell">
        <img id="frame" alt="latest Recast Lens frame" />
        <div id="boxLayer" aria-label="detected object boxes"></div>
      </div>
    </main>
    <footer>
      <span id="meta">No frame yet.</span>
      <span>
        <button id="objects" type="button">Identify objects</button>
        <button class="ghost" id="track" type="button">Live tracking off</button>
        <button id="interpret" type="button">What am I seeing?</button>
      </span>
    </footer>
    <div id="aiMeta">AI overlay: waiting for object detection.</div>
    <div id="answer">No interpretation yet.</div>
    <script>
      let latestObjects = null;
      let hasFrame = false;
      let trackingEnabled = false;
      let detectionInFlight = false;

      function renderObjectBoxes(data) {
        latestObjects = data || latestObjects;
        const layer = document.getElementById('boxLayer');
        const aiMeta = document.getElementById('aiMeta');
        layer.innerHTML = '';
        const objects = latestObjects?.objects || [];
        const size = latestObjects?.image_size || {};
        if (!objects.length || !size.width || !size.height) {
          aiMeta.textContent = 'AI overlay: no detected objects yet.';
          return;
        }
        const age = latestObjects?.created_at_iso ? Math.max(0, Math.round((Date.now() - new Date(latestObjects.created_at_iso).getTime()) / 1000)) : null;
        const summary = Object.entries(latestObjects.summary || {}).map(([label, count]) => `${label}: ${count}`).join(' · ');
        aiMeta.textContent = `AI overlay: ${objects.length} objects${summary ? ' · ' + summary : ''}${age !== null ? ' · updated ' + age + 's ago' : ''}`;
        for (const [index, obj] of objects.slice(0, 20).entries()) {
          const box = obj.bbox_xyxy || [];
          if (box.length !== 4) continue;
          const [x1, y1, x2, y2] = box;
          const el = document.createElement('div');
          el.className = 'object-box';
          const left = Math.max(0, Math.min(100, (x1 / size.width) * 100));
          const top = Math.max(0, Math.min(100, (y1 / size.height) * 100));
          const width = Math.max(1, Math.min(100 - left, ((x2 - x1) / size.width) * 100));
          const height = Math.max(1, Math.min(100 - top, ((y2 - y1) / size.height) * 100));
          el.style.left = left + '%';
          el.style.top = top + '%';
          el.style.width = width + '%';
          el.style.height = height + '%';
          const label = document.createElement('span');
          label.textContent = `${obj.label} ${Math.round((obj.confidence || 0) * 100)}%`;
          el.appendChild(label);
          layer.appendChild(el);
        }
      }

      async function tick() {
        const ts = Date.now();
        const img = document.getElementById('frame');
        const state = document.getElementById('state');
        const meta = document.getElementById('meta');
        try {
          const res = await fetch('/api/recast-lens/status?ts=' + ts, { cache: 'no-store' });
          const data = await res.json();
          if (data.has_frame) {
            hasFrame = true;
            img.src = '/api/recast-lens/latest.jpg?ts=' + ts;
            if (data.objects) renderObjectBoxes(data.objects);
            if (data.tracking) {
              trackingEnabled = Boolean(data.tracking.enabled);
              document.getElementById('track').textContent = trackingEnabled ? 'Live tracking on' : 'Live tracking off';
            }
            state.textContent = data.is_live ? 'live' : 'stale';
            state.className = data.is_live ? 'pill' : 'pill stale';
            const latest = data.latest || {};
            const age = typeof data.frame_age_s === 'number' ? ` · ${data.frame_age_s}s old` : '';
            meta.textContent = `frame ${latest.frame_count || data.frame_count || '?'} · ${latest.bytes || '?'} bytes · ${latest.received_at_iso || 'unknown time'}${age} · ${latest.session_id || 'no session'}`;
          } else {
            hasFrame = false;
            state.textContent = 'waiting';
            state.className = 'pill offline';
            meta.textContent = 'No frame received yet.';
            renderObjectBoxes({ objects: [] });
          }
        } catch (e) {
          state.textContent = 'offline';
          state.className = 'pill offline';
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
      async function detectObjects({ quiet = false } = {}) {
        if (detectionInFlight) return;
        detectionInFlight = true;
        const button = document.getElementById('objects');
        const answer = document.getElementById('answer');
        button.disabled = true;
        button.textContent = 'Detecting...';
        if (!quiet) answer.textContent = 'Running local YOLO object detector...';
        try {
          const res = await fetch('/api/recast-lens/detect-objects', { method: 'POST' });
          const data = await res.json();
          if (data.error) {
            answer.textContent = data.error;
          } else {
            const summary = Object.entries(data.summary || {}).map(([label, count]) => `${label}: ${count}`).join(' · ');
            if (!quiet) answer.textContent = `${data.count} objects detected${summary ? ': ' + summary : '.'}`;
            renderObjectBoxes(data);
          }
        } catch (e) {
          answer.textContent = e.message;
        } finally {
          detectionInFlight = false;
          button.disabled = false;
          button.textContent = 'Identify objects';
        }
      }
      async function toggleTracking() {
        const next = !trackingEnabled;
        const button = document.getElementById('track');
        button.disabled = true;
        button.textContent = next ? 'Starting tracking...' : 'Stopping tracking...';
        try {
          const res = await fetch('/api/recast-lens/tracking', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: next })
          });
          const data = await res.json();
          trackingEnabled = Boolean(data.tracking?.enabled);
          button.textContent = trackingEnabled ? 'Live tracking on' : 'Live tracking off';
        } catch (e) {
          document.getElementById('answer').textContent = e.message;
          button.textContent = trackingEnabled ? 'Live tracking on' : 'Live tracking off';
        } finally {
          button.disabled = false;
        }
      }
      document.getElementById('interpret').addEventListener('click', interpret);
      document.getElementById('objects').addEventListener('click', detectObjects);
      document.getElementById('track').addEventListener('click', toggleTracking);
      tick();
      setInterval(tick, """ + str(VIEWER_REFRESH_INTERVAL_MS) + """);
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
