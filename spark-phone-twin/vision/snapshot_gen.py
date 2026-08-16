"""
snapshot_gen.py - snapshot-to-repurposing image generation for the Spark
digital-twin pipeline.

Flow: pull a phone's latest live frame from the phone bridge -> derive a
depth map from it (YOLO26-depth, same call spark_app.py makes) -> run
interior_gen.generate() to re-render that same room as a different preset
use -> write the PNG under ~/plans/generated/ and record it in
~/plans/generated/index.json so the desktop app can just read the index and
show the newest generated image per device.

Generation runs in a single background worker thread so callers never block:

    from snapshot_gen import request, status
    job = request("b3jv40d", "dentist office")   # returns immediately
    ...
    status(job["job_id"])                        # poll

index.json is a JSON list of records, one per completed generation, newest
last:
    {
      "device":      "b3jv40d",
      "preset":      "dentist office",
      "source_frame": "/home/acer01/plans/generated/src_b3jv40d_1755400000.jpg",
      "output_path":  "/home/acer01/plans/generated/b3jv40d_dentist_office.png",
      "timestamp":    1755400012.3,
      "seconds":      4.6
    }
The desktop app wants "the newest image per device": filter the list to the
max-timestamp record per `device` (not per device+preset), since a given
device's most recent generation is whatever preset was last requested for it.

Memory guard: this box has no swap cushion worth relying on and the SD1.5 +
ControlNet pipeline needs ~7 GB resident. Before the FIRST load, free_gib()
must report >= MIN_FREE_GIB or the job fails with a clear "not enough memory"
error instead of risking the box. Once loaded, the pipeline stays resident
(interior_gen.py already caches it in a module-level global) and later jobs
skip the check.

CLI (also used for local validation without a live phone -- see
--local-frame):
    python3 snapshot_gen.py <device_id> <preset>
    python3 snapshot_gen.py --local-frame /path/to/frame.jpg --label lobby --preset "dentist office"
"""

import argparse
import json
import os
import queue
import ssl
import subprocess
import sys
import threading
import time
import urllib.request

import cv2
import numpy as np

import interior_gen

BRIDGE = "https://127.0.0.1:8099"
PLANS = os.path.expanduser("~/plans")
OUT_DIR = os.path.join(PLANS, "generated")
INDEX_PATH = os.path.join(OUT_DIR, "index.json")

MIN_FREE_GIB = 8  # interior_gen's SD1.5 + ControlNet pipeline needs ~7 GB resident

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE  # self-signed bridge cert, per the phone bridge's own design

# ---------------------------------------------------------------------------
# YOLO26 depth -- same model + call pattern spark_app.py and phone_bridge.py
# use, so a snapshot's depth is consistent with the rest of the twin. Kept
# resident once loaded, same as interior_gen's own SD pipeline.
# ---------------------------------------------------------------------------
_depth_model = None
_depth_model_name = "none"


def _get_depth_model():
    global _depth_model, _depth_model_name
    if _depth_model is not None:
        return _depth_model
    from ultralytics import YOLO
    for cand in ("yolo26s-depth.pt", "yolo26n-depth.pt"):
        p = os.path.expanduser("~/arlo-vision/%s" % cand)
        if os.path.exists(p):
            _depth_model = YOLO(p)
            _depth_model_name = cand
            print("[snapshot_gen] depth model: %s" % cand, file=sys.stderr)
            break
    return _depth_model


def infer_depth(img_bgr):
    """Metric-ish depth map for a BGR frame, or None. Mirrors spark_app.py's
    infer_depth(): probes result attrs rather than assuming ultralytics'
    exact output shape."""
    model = _get_depth_model()
    if model is None:
        return None
    try:
        r = model.predict(img_bgr, verbose=False)[0]
        for attr in ("depth", "depths"):
            o = getattr(r, attr, None)
            if o is None:
                continue
            d = o.data.cpu().numpy() if hasattr(o, "data") else np.asarray(o)
            d = np.squeeze(d).astype(np.float32)
            if d.ndim == 2:
                return d
    except Exception as e:
        print("[snapshot_gen] depth inference failed: %s" % str(e)[:150], file=sys.stderr)
    return None


def free_gib():
    """Available RAM in whole GiB, from `free -g`'s 'available' column."""
    try:
        o = subprocess.run(["free", "-g"], capture_output=True, text=True, timeout=5).stdout
        return int([l for l in o.splitlines() if l.startswith("Mem:")][0].split()[6])
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Phone bridge fetch
# ---------------------------------------------------------------------------
def fetch_latest_frame(device_id, timeout=5):
    """GET /latest/<id> from the phone bridge -> decoded BGR image. Raises
    RuntimeError with a clear message on any failure (unknown device, bridge
    down, corrupt JPEG)."""
    url = "%s/latest/%s" % (BRIDGE, device_id)
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=_ssl_ctx) as r:
            if r.status != 200:
                raise RuntimeError("bridge returned HTTP %d for device %r" % (r.status, device_id))
            buf = np.frombuffer(r.read(), np.uint8)
    except Exception as e:
        raise RuntimeError("could not reach phone bridge %s: %s" % (url, e))
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("bridge returned an undecodable frame for device %r" % device_id)
    return img


def list_devices(timeout=5):
    """GET /devices -> list of device dicts (id, name, age, live, ...)."""
    url = "%s/devices" % BRIDGE
    with urllib.request.urlopen(url, timeout=timeout, context=_ssl_ctx) as r:
        return json.loads(r.read().decode()).get("devices", [])


# ---------------------------------------------------------------------------
# index.json maintenance (atomic write so a concurrently-reading desktop app
# never sees a half-written file)
# ---------------------------------------------------------------------------
_index_lock = threading.Lock()


def _append_index_record(record):
    os.makedirs(OUT_DIR, exist_ok=True)
    with _index_lock:
        try:
            data = json.load(open(INDEX_PATH)) if os.path.exists(INDEX_PATH) else []
        except Exception:
            data = []
        data.append(record)
        tmp = INDEX_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, INDEX_PATH)


def _slug(preset):
    return preset.strip().lower().replace(" ", "_").replace("/", "_")


# ---------------------------------------------------------------------------
# Core generation (shared by the live-device path and the local-frame test
# path so both exercise the exact same depth + interior_gen logic).
# ---------------------------------------------------------------------------
def _generate_for_frame(device_label, img_bgr, preset, source_frame_path=None):
    """img_bgr: decoded BGR frame already in hand (from the bridge or a local
    file). Derives depth via YOLO26, writes it as .npy, calls
    interior_gen.generate(), updates index.json. Returns the index record."""
    if preset not in interior_gen.PRESETS:
        raise ValueError("unknown preset %r; options: %s" % (preset, list(interior_gen.PRESETS)))

    os.makedirs(OUT_DIR, exist_ok=True)

    if _get_depth_model() is None:
        # First load can legitimately fail to find weights; interior_gen's
        # own photo path would silently mask this. Fail loudly here since
        # spark_app.py's depth model is what this contract promises.
        raise RuntimeError("no YOLO26 depth model found under ~/arlo-vision/ "
                            "(expected yolo26s-depth.pt or yolo26n-depth.pt)")

    # Memory guard: only matters before interior_gen's SD pipeline is first
    # loaded; after that it stays resident and this check is skipped.
    if interior_gen._pipe is None:
        fg = free_gib()
        if fg < MIN_FREE_GIB:
            raise RuntimeError(
                "only %dGiB RAM available (<%dGiB) - refusing to load the "
                "image-generation pipeline; this box has no swap cushion to "
                "spare" % (fg, MIN_FREE_GIB))

    t0 = time.time()
    depth = infer_depth(img_bgr)
    if depth is None:
        raise RuntimeError("YOLO26 depth inference returned nothing for this frame")

    depth_npy_path = os.path.join(OUT_DIR, "depth_%s_%d.npy" % (device_label, int(t0)))
    np.save(depth_npy_path, depth)

    out_path = os.path.join(OUT_DIR, "%s_%s.png" % (device_label, _slug(preset)))
    interior_gen.generate(depth_npy_path, preset, out_path)
    elapsed = time.time() - t0

    record = dict(
        device=device_label,
        preset=preset,
        source_frame=source_frame_path or depth_npy_path,
        output_path=out_path,
        timestamp=time.time(),
        seconds=round(elapsed, 2),
    )
    _append_index_record(record)
    return record


def _generate_from_device(device_id, preset):
    img = fetch_latest_frame(device_id)
    ts = int(time.time())
    src_path = os.path.join(OUT_DIR, "src_%s_%d.jpg" % (device_id, ts))
    os.makedirs(OUT_DIR, exist_ok=True)
    cv2.imwrite(src_path, img)
    return _generate_for_frame(device_id, img, preset, source_frame_path=src_path)


def generate_from_local_file(frame_path, preset, device_label=None):
    """Test/validation entry point: run the exact same depth+generate path
    against a file on disk instead of a live phone. Used by the CLI's
    --local-frame flag; not part of the request()/status() queue contract."""
    img = cv2.imread(frame_path)
    if img is None:
        raise RuntimeError("could not read image %r" % frame_path)
    label = device_label or os.path.splitext(os.path.basename(frame_path))[0]
    return _generate_for_frame(label, img, preset, source_frame_path=frame_path)


# ---------------------------------------------------------------------------
# Queue / worker: request() never blocks. One worker thread processes jobs
# serially (the GPU pipeline is single-resident anyway, so no benefit to more
# workers, and it keeps peak memory predictable on this no-swap-cushion box).
# ---------------------------------------------------------------------------
_job_queue = queue.Queue()
_jobs = {}  # job_id -> status dict
_jobs_lock = threading.Lock()
_worker_thread = None
_job_counter = 0
_counter_lock = threading.Lock()


def _next_job_id():
    global _job_counter
    with _counter_lock:
        _job_counter += 1
        return "job%04d" % _job_counter


def _worker_loop():
    while True:
        job_id, device_id, preset = _job_queue.get()
        with _jobs_lock:
            _jobs[job_id] = dict(job_id=job_id, device=device_id, preset=preset,
                                  status="running", started=time.time())
        try:
            record = _generate_from_device(device_id, preset)
            with _jobs_lock:
                _jobs[job_id] = dict(job_id=job_id, device=device_id, preset=preset,
                                      status="done", finished=time.time(), **record)
        except Exception as e:
            with _jobs_lock:
                _jobs[job_id] = dict(job_id=job_id, device=device_id, preset=preset,
                                      status="error", error=str(e)[:300], finished=time.time())
        finally:
            _job_queue.task_done()


def _ensure_worker():
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
        _worker_thread.start()


def request(device_id, preset):
    """Enqueue a generation job for `device_id` at `preset` and return
    immediately with {job_id, status: 'queued'}. The actual pull-frame ->
    depth -> generate work happens on a background worker thread; poll
    status(job_id) to see progress. Does not block even if the SD pipeline
    is still loading or another job is running."""
    if preset not in interior_gen.PRESETS:
        return dict(job_id=None, status="error",
                     error="unknown preset %r; options: %s" % (preset, list(interior_gen.PRESETS)))
    _ensure_worker()
    job_id = _next_job_id()
    with _jobs_lock:
        _jobs[job_id] = dict(job_id=job_id, device=device_id, preset=preset,
                              status="queued", queued=time.time())
    _job_queue.put((job_id, device_id, preset))
    return dict(job_id=job_id, status="queued")


def status(job_id=None):
    """With a job_id: that job's status dict. Without one: the full job
    table (job_id -> status dict) plus queue depth, so a caller can poll
    overall worker health."""
    with _jobs_lock:
        if job_id is not None:
            return dict(_jobs.get(job_id, {"job_id": job_id, "status": "unknown"}))
        return dict(jobs=dict(_jobs), queue_depth=_job_queue.qsize())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("device_id", nargs="?", help="phone bridge device id")
    ap.add_argument("preset", nargs="?", help="see interior_gen.PRESETS")
    ap.add_argument("--local-frame", help="bypass the phone bridge; generate from a local image file")
    ap.add_argument("--preset", dest="preset_flag", help="preset (for --local-frame mode)")
    ap.add_argument("--label", help="device label to use for --local-frame output naming")
    ap.add_argument("--list-devices", action="store_true")
    a = ap.parse_args()

    if a.list_devices:
        print(json.dumps(list_devices(), indent=2))
        sys.exit(0)

    if a.local_frame:
        preset = a.preset_flag or a.preset
        if not preset:
            print("need --preset for --local-frame mode"); sys.exit(1)
        rec = generate_from_local_file(a.local_frame, preset, device_label=a.label)
        print(json.dumps(rec, indent=2))
        sys.exit(0)

    if not a.device_id or not a.preset:
        print("usage: snapshot_gen.py <device_id> <preset>")
        print("   or: snapshot_gen.py --local-frame <path> --preset <preset> [--label NAME]")
        print("   or: snapshot_gen.py --list-devices")
        sys.exit(1)

    job = request(a.device_id, a.preset)
    print("queued:", job)
    while True:
        s = status(job["job_id"])
        if s.get("status") in ("done", "error", "unknown"):
            print(json.dumps(s, indent=2))
            break
        time.sleep(0.5)
