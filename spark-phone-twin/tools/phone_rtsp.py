#!/usr/bin/env python3
"""phone_rtsp.py - bridges phone_bridge.py JPEG frames into RTSP via MediaMTX.

phone_bridge.py (https://127.0.0.1:8099) receives single JPEG frames pushed by
phones' browsers and exposes them at /devices (JSON list) and /latest/<id>
(newest JPEG for that device). VSS/VST need RTSP, not a JPEG-poll endpoint, so
this script bridges the gap:

  poll /devices every SCAN_INTERVAL
    -> for each *live* device, spawn/maintain one ffmpeg child that:
         - is fed a steady stream of JPEGs on stdin (fetched from /latest/<id>
           at FETCH_HZ; the last frame is repeated if no new one arrived, to
           keep the mjpeg demuxer's clock continuous)
         - decodes as -f mjpeg, encodes libx264 (ultrafast/zerolatency)
         - publishes rtsp://127.0.0.1:8554/phone_<id> into MediaMTX
    -> devices that go stale/disappear have their worker stopped

Restarts a dead ffmpeg with exponential backoff. SIGTERM/SIGINT stop every
worker and kill its ffmpeg child by PID (never pkill/pgrep). Self-signed TLS
on the bridge is not verified (LAN-only, known origin).

    ~/arlo-vision/bin/python ~/arlo-vision/phone_rtsp.py
"""
import json
import os
import shutil
import signal
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_PATH = HERE / "phone_rtsp.log"
PID_PATH = HERE / "phone_rtsp.pid"

BRIDGE_BASE = "https://127.0.0.1:8099"
MTX_HOST, MTX_PORT = "127.0.0.1", 8554
FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"

SCAN_INTERVAL = 5.0     # how often to re-poll /devices for live/gone phones
FETCH_HZ = 5.0          # how often to pull /latest/<id> and feed ffmpeg
BASE_BACKOFF = 3.0
MAX_BACKOFF = 30.0

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

stop_event = threading.Event()
workers = {}          # device id -> DeviceWorker
_log_lock = threading.Lock()


def log(msg):
    line = "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with _log_lock:
        with open(LOG_PATH, "a") as f:
            f.write(line)
    sys.stdout.write(line)
    sys.stdout.flush()


def fetch_devices():
    try:
        req = urllib.request.Request(BRIDGE_BASE + "/devices")
        with urllib.request.urlopen(req, timeout=4, context=SSL_CTX) as r:
            return json.loads(r.read()).get("devices", [])
    except Exception as e:
        log("devices poll failed: %s" % e)
        return []


def fetch_latest(dev_id):
    try:
        req = urllib.request.Request(BRIDGE_BASE + "/latest/" + dev_id)
        with urllib.request.urlopen(req, timeout=3, context=SSL_CTX) as r:
            return r.read()
    except Exception:
        return None


class DeviceWorker(threading.Thread):
    def __init__(self, dev_id, name):
        super().__init__(daemon=True)
        self.dev_id = dev_id
        self.name = name
        self.stop = threading.Event()
        self.restarts = 0
        self.proc = None

    def run(self):
        backoff = BASE_BACKOFF
        while not self.stop.is_set() and not stop_event.is_set():
            ok = self._run_once()
            if self.stop.is_set() or stop_event.is_set():
                break
            self.restarts += 1
            log("phone_%s: ffmpeg cycle ended, restart #%d in %.0fs" %
                (self.dev_id, self.restarts, backoff))
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
        log("phone_%s: worker stopped (%d total restarts)" % (self.dev_id, self.restarts))

    def _run_once(self):
        local_url = "rtsp://%s:%d/phone_%s" % (MTX_HOST, MTX_PORT, self.dev_id)
        cmd = [FFMPEG, "-nostdin", "-loglevel", "warning",
               "-f", "mjpeg", "-use_wallclock_as_timestamps", "1", "-i", "pipe:0",
               "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
               "-pix_fmt", "yuv420p", "-r", str(int(FETCH_HZ)),
               "-g", "10", "-keyint_min", "10",  # short GOP so RTSP joiners get an IDR fast
               "-f", "rtsp", "-rtsp_transport", "tcp", local_url]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log("phone_%s: failed to spawn ffmpeg: %s" % (self.dev_id, e))
            return False
        self.proc = proc
        log("phone_%s: ffmpeg started pid=%d -> %s" % (self.dev_id, proc.pid, local_url))

        last_frame = None
        period = 1.0 / FETCH_HZ
        ok = True
        while not self.stop.is_set() and not stop_event.is_set():
            if proc.poll() is not None:
                log("phone_%s: ffmpeg exited code=%s" % (self.dev_id, proc.returncode))
                ok = False
                break
            frame = fetch_latest(self.dev_id)
            if frame:
                last_frame = frame
            if last_frame is None:
                time.sleep(0.5)
                continue
            try:
                proc.stdin.write(last_frame)
                proc.stdin.flush()
            except Exception as e:
                log("phone_%s: stdin write failed: %s" % (self.dev_id, e))
                ok = False
                break
            time.sleep(period)

        self._kill(proc)
        return ok

    def _kill(self, proc):
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        except Exception:
            pass

    def request_stop(self):
        self.stop.set()
        self._kill(self.proc)


def reconcile():
    devs = fetch_devices()
    live_ids = set()
    for d in devs:
        if not d.get("live"):
            continue
        did = d["id"]
        live_ids.add(did)
        if did not in workers:
            w = DeviceWorker(did, d.get("name", "phone"))
            workers[did] = w
            w.start()
            log("phone_%s: new live device (name=%s), starting worker" % (did, d.get("name")))
    for did in list(workers):
        if did not in live_ids:
            w = workers.pop(did)
            log("phone_%s: no longer live, stopping worker" % did)
            w.request_stop()


def handle_signal(signum, _frame):
    log("received signal %d, shutting down" % signum)
    stop_event.set()
    for w in list(workers.values()):
        w.request_stop()


def main():
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    PID_PATH.write_text(str(os.getpid()) + "\n")
    log("phone_rtsp starting pid=%d, mediamtx=%s:%d, bridge=%s" %
        (os.getpid(), MTX_HOST, MTX_PORT, BRIDGE_BASE))

    try:
        while not stop_event.is_set():
            reconcile()
            time.sleep(SCAN_INTERVAL)
    except KeyboardInterrupt:
        stop_event.set()

    for w in list(workers.values()):
        w.request_stop()
    for w in list(workers.values()):
        w.join(timeout=10)
    try:
        PID_PATH.unlink()
    except Exception:
        pass
    log("phone_rtsp stopped")


if __name__ == "__main__":
    main()
