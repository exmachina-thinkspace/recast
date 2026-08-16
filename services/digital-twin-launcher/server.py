"""Digital Twin launcher -- a tiny, standalone, additive-only bridge between
the React frontend's "Digital Twin" option and Michael's spark-phone-twin
pipeline (deployed on the box at ~/arlo-vision). Does NOT modify
spark-phone-twin's own code, does NOT modify any other existing backend
service -- it only shells out to the scripts exactly as documented in
spark-phone-twin/README.md (scripts/start_bridge.sh, scripts/start_app.sh),
and reports status.

The pipeline itself:
  - phone_bridge.py: a real HTTP server on :8099 phones connect to (QR join)
  - spark_app.py: a NATIVE desktop app that renders the live 3D map on the
    box's own physical display (X11), not a web page -- so this launcher
    starts it, but the visual only appears on the Spark's own screen, same
    as the earlier Firefox-on-the-box-display work this session.

Endpoints:
  GET  /health
  POST /start   -> runs start_bridge.sh then start_app.sh, returns their output
  GET  /status  -> is spark_app.py running? is phone_bridge.py running?
                   how many phones are connected? (proxies phone_bridge's
                   own /devices endpoint on :8099)

Usage:
  python3 server.py --port 8611 --twin-dir ~/arlo-vision
"""
import os
import ssl
import sys
import json
import argparse
import subprocess
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TWIN_DIR = None
# phone_bridge.py serves HTTPS with a self-signed cert (phones require a
# secure context for camera/motion sensor APIs) -- match that here, same
# unverified-cert pattern as connecting to any other self-signed local dev
# service on this box.
PHONE_BRIDGE_URL = "https://127.0.0.1:8099"
_INSECURE_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_INSECURE_CTX.check_hostname = False
_INSECURE_CTX.verify_mode = ssl.CERT_NONE


def _pid_running(pattern):
    try:
        out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5)
        return [p for p in out.stdout.split() if p]
    except Exception:
        return []


def _run_script(name, timeout):
    path = os.path.join(TWIN_DIR, "scripts", name)
    if not os.path.exists(path):
        return {"ok": False, "error": "script not found: %s" % path}
    try:
        proc = subprocess.run(["bash", path], capture_output=True, text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-2000:]}
    except subprocess.TimeoutExpired as e:
        out = e.stdout
        out = out.decode(errors="replace") if isinstance(out, bytes) else (out or "")
        return {"ok": False, "error": "timed out after %ss" % timeout, "stdout": out[-2000:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _phone_bridge_devices():
    try:
        with urllib.request.urlopen(PHONE_BRIDGE_URL + "/devices", timeout=3, context=_INSECURE_CTX) as r:
            return json.load(r)
    except Exception as e:
        return {"error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"ok": True})
            return
        if self.path == "/status":
            app_pids = _pid_running(r"spark_app\.py")
            bridge_pids = _pid_running(r"phone_bridge\.py")
            devices = _phone_bridge_devices() if bridge_pids else None
            self._send_json(200, {
                "ok": True,
                "app_running": bool(app_pids),
                "bridge_running": bool(bridge_pids),
                "devices": devices,
                "note": "The live 3D map renders on the Spark's own connected "
                        "display (spark_app.py is a native desktop app, not a "
                        "web page). Phones join by scanning the QR code shown "
                        "there.",
            })
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/start":
            bridge_result = _run_script("start_bridge.sh", timeout=30)
            app_result = _run_script("start_app.sh", timeout=45)
            ok = bool(bridge_result.get("ok")) and bool(app_result.get("ok"))
            self._send_json(200 if ok else 500, {
                "ok": ok,
                "bridge": bridge_result,
                "app": app_result,
            })
            return
        self._send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[digital-twin-launcher] " + (fmt % args) + "\n")


def main():
    global TWIN_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8611)
    ap.add_argument("--twin-dir", default=os.path.expanduser("~/arlo-vision"))
    a = ap.parse_args()
    TWIN_DIR = a.twin_dir
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    print("[digital-twin-launcher] listening on http://0.0.0.0:%d  twin_dir=%s" % (a.port, TWIN_DIR))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
