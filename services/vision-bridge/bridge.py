"""RTSP -> detection -> sensor_observation JSON -> API sink.

Namratha's vision-bridge (responsibility(1).md section 3.2 / 5.2). Reads
configs/cameras.yaml, samples each active camera on an interval, emits one
zone_occupancy event per camera per sample, validated against
packages/contracts/sensor_observation.schema.json, and POSTs each event to
the sink API (services/api-sink/) -- a stand-in for Michael's real
services/api until that exists.

Two modes:
  --fixture   No camera/model dependency. Emits synthetic-but-plausible
              events on schedule so Michael (or this repo's sink) can be
              built against the contract before live detection is wired up.
  (default)   Real mode. Requires opencv + ultralytics + RTSP env vars set,
              matches the pattern already proven in
              spark-3d-pipeline/src/twin/build_vitals.py's observe().

Usage:
  python3 bridge.py --fixture --duration 30 --sink http://127.0.0.1:8600
  python3 bridge.py --duration 120 --sink http://127.0.0.1:8600
"""
import os
import sys
import time
import json
import random
import argparse
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))
from contract import make_event, ContractError

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CAMERAS_YAML = os.path.join(REPO_ROOT, "configs", "cameras.yaml")
LOG_PATH = os.path.join(os.path.dirname(__file__), "events.jsonl")


def load_cameras():
    import yaml
    with open(CAMERAS_YAML) as f:
        cfg = yaml.safe_load(f)
    active = [c for c in cfg["cameras"] if c["status"] == "active"]
    return cfg["building_id"], active


def post_event(sink_url, event):
    if not sink_url:
        return None
    payload = json.dumps(event).encode()
    req = urllib.request.Request(
        sink_url.rstrip("/") + "/observations",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.URLError as e:
        print(f"  [sink unreachable: {e}]", file=sys.stderr)
        return None


def log_event(event):
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")


def run_fixture(building_id, cameras, duration, interval, sink_url):
    end = time.time() + duration
    i = 0
    while time.time() < end:
        for cam in cameras:
            count = max(0, round(random.gauss(2.0, 1.5)))
            conf = round(random.uniform(0.75, 0.97), 2)
            event = make_event(
                event_id=f"ev_{int(time.time())}_{cam['id']}_{i}",
                building_id=building_id,
                space_id=cam["zone"],
                event_type="zone_occupancy",
                value=count,
                unit="people",
                evidence_tier="T1",
                confidence=conf,
                source=f"camera:{cam['id']}",
            )
            log_event(event)
            status = post_event(sink_url, event)
            print(f"[fixture] {cam['id']}: {count} people conf={conf} -> sink {status}")
        i += 1
        time.sleep(interval)


def run_live(building_id, cameras, duration, interval, sink_url):
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    import cv2
    from ultralytics import YOLO

    model = YOLO("yolo11m.pt")
    caps = {}
    for cam in cameras:
        url = os.environ.get(cam["rtsp_env"], "")
        if not url:
            print(f"[skip] {cam['id']}: {cam['rtsp_env']} not set in environment", file=sys.stderr)
            continue
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        caps[cam["id"]] = (cap, cam)

    if not caps:
        print("No cameras available (RTSP env vars unset). Use --fixture instead.", file=sys.stderr)
        return

    end = time.time() + duration
    i = 0
    while time.time() < end:
        for cam_id, (cap, cam) in caps.items():
            ok, frame = cap.read()
            if not ok:
                continue
            result = model.predict(frame, imgsz=1280, classes=[0], conf=0.35,
                                     device=0, verbose=False)[0]
            count = len(result.boxes)
            confs = result.boxes.conf.tolist() if count else [0.0]
            mean_conf = round(sum(confs) / len(confs), 2) if confs else 0.0
            event = make_event(
                event_id=f"ev_{int(time.time())}_{cam_id}_{i}",
                building_id=building_id,
                space_id=cam["zone"],
                event_type="zone_occupancy",
                value=count,
                unit="people",
                evidence_tier="T1",
                confidence=mean_conf if count else 0.5,
                source=f"camera:{cam_id}",
            )
            log_event(event)
            status = post_event(sink_url, event)
            print(f"[live] {cam_id}: {count} people conf={mean_conf} -> sink {status}")
        i += 1
        time.sleep(interval)

    for cap, _ in caps.values():
        cap.release()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true", help="synthetic events, no camera/model needed")
    ap.add_argument("--duration", type=int, default=60, help="seconds to run")
    ap.add_argument("--interval", type=int, default=5, help="seconds between samples")
    ap.add_argument("--sink", default=None, help="e.g. http://127.0.0.1:8600")
    args = ap.parse_args()

    building_id, cameras = load_cameras()
    if not cameras:
        print("No active cameras in configs/cameras.yaml", file=sys.stderr)
        sys.exit(1)

    try:
        if args.fixture:
            run_fixture(building_id, cameras, args.duration, args.interval, args.sink)
        else:
            run_live(building_id, cameras, args.duration, args.interval, args.sink)
    except ContractError as e:
        print(f"CONTRACT VIOLATION (this should never happen): {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
