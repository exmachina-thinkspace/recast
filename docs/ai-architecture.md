# AI Architecture

Owner: Namratha. Status: living document, updated 2026-08-15.

## Technical rule

**AI observes and explains. Deterministic code calculates the BHI.** No model output ever writes directly into a vital score. This is enforced by keeping detection, scoring, and explanation as three separable stages with a typed contract between the first two.

## Pipeline

```text
Arlo cameras
    -> Scrypted (RTSP rebroadcast)
    -> services/vision-bridge/bridge.py   (YOLO11m person detection, per-camera sampling loop)
    -> packages/contracts/sensor_observation.schema.json   (validated, typed events)
    -> services/api-sink/  (placeholder for Michael's services/api)
    -> src/twin/build_vitals.py   (deterministic BHI: weighted vitals, T0-T2 evidence tiers)
    -> local LLM grounded explanation (Qwen3.6-35B-A3B-NVFP4 via vLLM, port 8000)
    -> judge-facing narrative
```

Two independent stages consume camera data:

1. **Detection -> scoring.** `bridge.py` emits `zone_occupancy` events (person count, confidence, evidence tier T1). `build_vitals.py` aggregates these into the `use_utilization` and `community` vitals using fixed weights and a fixed BOMA-style capacity formula. No LLM is in this path.
2. **Scoring -> explanation.** Once a `build_vitals.json` scorecard exists, a local LLM narrates it in plain language for a judge, constrained to the JSON it's given (see `docs/model-evaluation.md` for grounding test results). The LLM never sees raw video and never computes a number that lands in the scorecard.

## Components

| Component | Path | Status |
|---|---|---|
| Plan -> 3D model | `spark-3d-pipeline/src/plans/` | Working — produces `building.glb`/`.ifc`/`.obj`, `scenegraph.json` |
| Depth/mesh from camera | `spark-3d-pipeline/src/vision/depth_to_mesh.py` | Working, but 2.5D only — see privacy/limits note below |
| Live person detection | `spark-3d-pipeline/src/vision/` (via `~/arlo-vision/live_detect.py` on Spark) | Working, ~33fps on 2F Lobby |
| Vision-bridge (typed events) | `services/vision-bridge/` | Built and tested — fixture mode proven locally and on Spark; live-camera mode blocked on RTSP token availability (see `docs/privacy-boundary.md`) |
| Sensor contract | `packages/contracts/sensor_observation.schema.json` | Frozen |
| API sink (placeholder) | `services/api-sink/` | Built as a stand-in for Michael's `services/api`; validates and stores events only, no scoring/spine logic |
| Deterministic BHI | `spark-3d-pipeline/src/twin/build_vitals.py` | Working single-building scorer with T0-T2 evidence tiers |
| Grounded explanation | Qwen3.6-35B-A3B-NVFP4 via vLLM | Tested — see `docs/model-evaluation.md` |
| Camera-zone calibration | — | **Not done.** Gate for real metric alignment and multi-camera tracking (see README, `configs/cameras.yaml`) |

## Model choices

- **Detection:** YOLO11m at `imgsz=1280`, class 0 (person) only. Chosen over YOLO11s (worse accuracy at same speed) and the 640 default (missed most people in a 2560x1440 fisheye frame).
- **Explanation:** `nvidia/Qwen3.6-35B-A3B-NVFP4`, already resident on the Spark via vLLM — zero additional memory cost, avoids repeating the OOM incident documented in `spark-3d-pipeline/SPARK-HANDOFF.md`. `nemotron-3.5-lightning` remains untested; deferred until the box has enough free memory to load it safely (see `docs/model-evaluation.md`).
- **Depth:** Depth-Anything-V2-Metric-Indoor. Known to be non-metric-accurate on this uncalibrated fisheye setup (5x spread in floor-to-ceiling estimates across cameras) — treated as visualization only, not a measurement source, and does not feed the BHI.

## Known gaps against the charter

- No tracking, line-crossing, or entry/exit counting yet — only per-sample zone occupancy.
- No camera-zone calibration, so `space_id` mapping is a static config assumption (`configs/cameras.yaml`), not a geometrically verified one.
- `services/api-sink` is a placeholder. It validates and stores events but does no building-spine resolution, scoring, or WebSocket/SSE push to a frontend — that's Michael's `services/api` and `apps/web`, neither of which exist in-repo yet.
- No live-use pulse to a frontend, because no frontend exists yet to receive one.
