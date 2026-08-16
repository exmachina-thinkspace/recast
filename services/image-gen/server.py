"""Image-gen HTTP service for Recast -- one small endpoint the frontend, the
voice agent, or any script can call without knowing about NIM vs hosted.

Wraps imagegen.py (FLUX.1-dev: local NIM on the Spark, or build.nvidia.com
fallback) in the same stdlib http.server style as the other services here.
Serves a tiny test page at / so anyone can verify it from a browser.

Endpoints:
  GET  /                    -> test page (index.html, same directory)
  GET  /health              -> {"ok": true, "nim": {...}, "hosted": {...}}
  POST /generate            -> JSON in, JSON out (image as base64)
  POST /generate?format=image -> JSON in, raw image bytes out (for <img>/blob)
  GET  /out/<file>          -> previously generated images (if --save-dir)

POST /generate body (all optional except prompt, or building+current_use+proposed_use):
  {
    "prompt": "...",                       # or:
    "building": "...", "current_use": "...", "proposed_use": "...", "extras": "...",
    "mode": "base" | "depth" | "canny",    # default base
    "image": "<base64 or data URL>",       # required for depth/canny
    "width": 1024, "height": 1024, "steps": 30, "cfg_scale": 3.5, "seed": 0,
    "backend": "auto" | "nim" | "hosted"
  }
Response: {"ok": true, "image_b64": ..., "mime": "image/png", "ext": "png",
           "backend": "nim", "elapsed_s": 31.2, "seed": 0, "prompt": ..., "file": "out/....png"}

Usage:
  python3 server.py --port 8602 [--save-dir out] [--nim-url http://127.0.0.1:8610]
"""
import os
import sys
import json
import time
import argparse
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imagegen  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = None
NIM_URL = imagegen.DEFAULT_NIM_URL


class Handler(BaseHTTPRequestHandler):
    server_version = "recast-imagegen/0.1"

    # -- helpers ------------------------------------------------------------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    def _send_bytes(self, status, data, mime):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode() or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"body is not valid JSON: {e}")

    def log_message(self, fmt, *args):  # quieter, timestamped
        sys.stderr.write("%s [imagegen] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # -- routes -------------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            info = imagegen.check(NIM_URL)
            info["ok"] = True
            info["save_dir"] = SAVE_DIR
            self._send_json(200, info)
            return
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send_bytes(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send_json(404, {"ok": False, "error": "index.html missing"})
            return
        if path.startswith("/out/") and SAVE_DIR:
            name = os.path.basename(path[len("/out/"):])
            fp = os.path.join(SAVE_DIR, name)
            if name and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    data = f.read()
                self._send_bytes(200, data, imagegen._sniff_mime(data))
                return
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        self._send_json(404, {"ok": False, "error": "unknown route"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/generate":
            self._send_json(404, {"ok": False, "error": "unknown route"})
            return
        want_image = urllib.parse.parse_qs(parsed.query).get("format", [""])[0] == "image"
        try:
            body = self._read_json()
            prompt = body.get("prompt")
            if not prompt and body.get("building"):
                prompt = imagegen.recast_prompt(
                    body["building"], body.get("current_use", "underused space"),
                    body.get("proposed_use", "a new use"), extras=body.get("extras", ""))
            if not prompt:
                raise ValueError("prompt (or building/current_use/proposed_use) is required")
            r = imagegen.generate(
                prompt,
                mode=body.get("mode", "base"),
                image=body.get("image"),
                width=body.get("width", 1024),
                height=body.get("height", 1024),
                steps=body.get("steps", 30),
                cfg_scale=body.get("cfg_scale"),
                seed=body.get("seed", 0),
                backend=body.get("backend"),
                nim_url=NIM_URL,
            )
        except (ValueError, imagegen.ImageGenError) as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return
        except Exception as e:  # keep the service up; report the failure
            self.log_message("generate failed: %r", e)
            self._send_json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        saved = None
        if SAVE_DIR:
            os.makedirs(SAVE_DIR, exist_ok=True)
            name = (time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
                    f"-{r.mode}-{r.seed}.{r.ext}")
            with open(os.path.join(SAVE_DIR, name), "wb") as f:
                f.write(r.image_bytes)
            saved = f"out/{name}"
        self.log_message("generated %s %dx%d via %s in %.1fs%s", r.mode, r.width, r.height,
                         r.backend, r.elapsed_s, f" -> {saved}" if saved else "")

        if want_image:
            self._send_bytes(200, r.image_bytes, r.mime)
            return
        out = r.to_json()
        out["ok"] = True
        out["file"] = saved
        self._send_json(200, out)


def main():
    global SAVE_DIR, NIM_URL
    ap = argparse.ArgumentParser(description="Recast image-gen service")
    ap.add_argument("--port", type=int, default=8602)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--save-dir", default=os.path.join(HERE, "out"),
                    help="where to keep generated images ('' to disable)")
    ap.add_argument("--nim-url", default=imagegen.DEFAULT_NIM_URL)
    a = ap.parse_args()
    SAVE_DIR = a.save_dir or None
    NIM_URL = a.nim_url
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"recast image-gen on http://{a.host}:{a.port}  nim={NIM_URL}  "
          f"hosted={'configured' if imagegen.hosted_configured() else 'no NVIDIA_API_KEY'}  "
          f"save_dir={SAVE_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
