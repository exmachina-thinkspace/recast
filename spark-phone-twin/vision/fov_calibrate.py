#!/usr/bin/env python3
"""
fov_calibrate.py -- Layer 10 (VSS Auto-Calibration / GeoCalib) prototype.

THE BUG: spark_app.py hardcodes a 68 degree horizontal FOV for every
phone (spark_app.py:246 and :273, "# typical phone hFOV"). That's a
guess applied uniformly to every joined phone regardless of make/model
or whether it's shooting through the main or ultra-wide lens. This
project has already been burned by a wrong FOV assumption once: an
earlier 120 deg guess vs an 86.9 deg measured value stretched all
back-projected geometry ~1.5x (see git history / diagnosis notes).

THE FIX (prototyped here, not wired in): estimate the REAL per-phone
HFOV from a captured frame using GeoCalib -- single-image calibration
via geometric deep learning (ECCV'24, cvg/GeoCalib). It already ships
inside the running `vss-auto-calibration` container (this machine's
Layer 10) with locally cached pinhole weights at
/home/auto-calibration-ms/models/geocalib/geocalib-pinhole.tar --
no internet fetch, no venv package changes, no sudo. We shell out to
`docker exec` so the arlo-vision venv's torch/numpy/opencv/ultralytics
versions are never touched.

Usage:
    python3 fov_calibrate.py frame1.jpg [frame2.jpg ...]

Wiring point (left to whoever owns phone_slam.py/phone_bridge.py):
call estimate_fov_robust() on a phone's first few frames right after
QR join, then feed hfov_deg into the fx/fy computation that today
uses the hardcoded 68.0 in spark_app.py.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess

CONTAINER = "vss-auto-calibration"
WEIGHTS = "/home/auto-calibration-ms/models/geocalib/geocalib-pinhole.tar"
GEOCALIB_PKG = "/home/auto-calibration-ms/submodules/GeoCalib"
HARDCODED_HFOV_DEG = 68.0  # what spark_app.py assumes today

_INFER_SNIPPET = f"""
import sys, json, torch
sys.path.insert(0, "{GEOCALIB_PKG}")
from geocalib import GeoCalib
from geocalib.utils import load_image
model = GeoCalib(weights="{WEIGHTS}")
device = "cuda" if torch.cuda.is_available() else "cpu"
model = model.to(device)
img = load_image(sys.argv[1]).to(device)
out = model.calibrate(img)
cam = out["camera"]
print(json.dumps({{
    "hfov_deg": torch.rad2deg(cam.hfov).item(),
    "vfov_deg": torch.rad2deg(cam.vfov).item(),
    "fx_px": cam.f[0, 0].item(),
    "fy_px": cam.f[0, 1].item(),
    "w": cam.size[0, 0].item(),
    "h": cam.size[0, 1].item(),
}}))
"""


def estimate_fov(image_path: str, timeout: int = 30) -> dict:
    """Run GeoCalib on a single frame inside the vss-auto-calibration
    container and return its estimated intrinsics."""
    remote_path = f"/tmp/fov_probe_{os.path.basename(image_path)}"
    subprocess.run(
        ["docker", "cp", image_path, f"{CONTAINER}:{remote_path}"],
        check=True, capture_output=True,
    )
    proc = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "python3", "-c", _INFER_SNIPPET, remote_path],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"GeoCalib failed on {image_path}: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def estimate_fov_robust(image_paths: list[str]) -> dict:
    """Average HFOV over several frames. Single-image GeoCalib estimates
    vary with scene content (empirically +-12 deg across very different
    rooms on this rig), so a real phone session should sample a handful
    of early frames and take the median rather than trust one shot."""
    results = [estimate_fov(p) for p in image_paths]
    hfovs = [r["hfov_deg"] for r in results]
    return {
        "n_frames": len(results),
        "hfov_deg_median": statistics.median(hfovs),
        "hfov_deg_mean": statistics.mean(hfovs),
        "hfov_deg_stdev": statistics.pstdev(hfovs) if len(hfovs) > 1 else 0.0,
        "per_frame": results,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+", help="one or more phone-frame JPEGs")
    args = ap.parse_args()

    result = estimate_fov_robust(args.images)
    print(json.dumps(result, indent=2))

    median = result["hfov_deg_median"]
    delta_pct = (median - HARDCODED_HFOV_DEG) / HARDCODED_HFOV_DEG * 100
    print(f"\nspark_app.py currently hardcodes HFOV = {HARDCODED_HFOV_DEG:.1f} deg for every phone.")
    print(f"Measured median HFOV from {result['n_frames']} frame(s): "
          f"{median:.1f} deg (stdev {result['hfov_deg_stdev']:.1f} deg across frames).")
    print(f"Delta vs hardcoded guess: {delta_pct:+.1f}% "
          f"-> every back-projected point/voxel from this phone is off by a comparable factor.")
