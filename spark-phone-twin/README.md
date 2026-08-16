# Spark Phone Twin

Builds a live 3D digital twin of 1700 Westlake Ave N on the DGX Spark, from the
architectural floor plans plus video and sensors streamed from ordinary phones.
No LiDAR, no fixed cameras: the plans supply the skeleton, and phones fill in
what the drawings do not record.

Runs entirely on the Spark (GB10, aarch64, CUDA 13).

## What it does

- **Plans to geometry.** Vector PDFs are extracted to walls, rooms and openings,
  the two storeys aligned, and the result extruded to a 3D building.
- **Phones as sensors.** A phone joins by scanning a QR badge on screen. It then
  streams camera frames, accelerometer, gyroscope, compass and GPS over HTTPS.
- **Position tracking.** Dead reckoning from the phone's IMU, corrected by the
  QR anchor and constrained by the floor plan — you cannot walk through a wall.
- **Scene understanding.** Metric monocular depth plus segmentation build a
  point cloud and a labelled scenegraph of what is actually in each room.
- **Repurposing.** Any captured photo can be reimagined as a dentist office,
  condo, coworking space and so on, generated locally with Stable Diffusion.

## Layout

    vision/    the desktop app, phone bridge, pose estimation, scenegraph
    plans/     PDF extraction, wall filtering, room polygons, validation
    tools/     audits — RoomPlan parity, NVIDIA stack verification
    scripts/   launchers (use these; see the note below)

Key modules:

| file | role |
|---|---|
| `vision/spark_app.py` | the desktop dashboard: 3D map, phones, scenegraph |
| `vision/phone_bridge.py` | HTTPS server the phones talk to; mobile web app |
| `vision/pdr.py` | pedestrian dead reckoning (step detection, heading, fusion) |
| `vision/qr_calibrate.py` | scale calibration against a printed QR of known size |
| `vision/phone_slam.py` | gravity alignment, plan-axis snapping, room placement |
| `vision/interior_gen.py` | Stable Diffusion + depth ControlNet repurposing |

## Running

    scripts/start_bridge.sh     # phone endpoint on :8099
    scripts/start_app.sh        # desktop dashboard

Use the scripts rather than launching the Python directly. Inline
`nohup`/`setsid` over ssh does **not** survive the session — the bridge dying
mid-test looks exactly like an application bug. `start_app.sh` also reclaims GPU
memory before starting, selecting by measured usage and never touching the
audited VSS stack, so image generation is not starved by an idle container.

## Testing pose tracking

    vision/test_pose_synth.py

Runs the **real** app against `vision/synth_bridge.py`, a fake phone walking a
scripted path — 60 Hz gait, compass, GPS, with stand-still and turn-in-place
segments. `spark_app` polls it through `BRIDGE_URL` and cannot tell the
difference.

This shape matters. Testing `pdr.py` on its own passed while the map stayed
frozen, because the breakage was in the wiring around the estimator, not the
estimator itself. The test asserts, in the order things actually failed:

1. the marker moves at all
2. it moves the right distance
3. it moves in the right direction
4. it does not teleport
5. it does not drift while standing still

Set `SHOT_EVERY=<seconds>` to have the app dump its own rendered canvas to
`~/plans/shots/`. X11 grabs come back black on this host, and a log line saying
a marker moved is not evidence that anything moved on screen.

Measured against ground truth:

| route | measured | truth | bearing error | stationary drift |
|---|---|---|---|---|
| 12 m east, 6 m north | 14.77 m | 13.42 m | 5° | 0.00 m |
| 12 m south, 6 m west | 13.45 m | 13.42 m | 12° | 0.00 m |

Dead reckoning drifts by nature; 10–25% distance error is expected and is what
the QR anchor exists to reset.

## Conventions worth knowing

- **Headings are compass bearings**: 0° = north = +y, 90° = east = +x. The step
  integrator originally used the maths convention and moved every phone 90° off.
- **Step detection needs a signed vertical signal.** Taking `norm(ax,ay,az)`
  rectifies the stride oscillation and doubles the step count; the linear
  acceleration is projected onto gravity instead.
- **Position is continuous.** All updates pass through one gate: a person cannot
  teleport, so implausible jumps are clamped along the same bearing rather than
  dropped. A QR scan is the single legitimate discontinuity.
- **Tracking must not require a picture.** PDR runs on its own clock, and a
  phone whose video has stalled still gets a marker on the map.
