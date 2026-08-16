# Recast Lens Bridge

Small Recast-owned GN100 bridge for the first webapp-based Recast Lens path.

It does not use Larix code and does not bind to the teammate prototype port `8099`.

Default port:

```text
8910
```

## Purpose

Accept frames from the Recast webapp's browser camera and expose the latest received frame/status for downstream Recast/VSS adapters.

Current flow:

```text
iPhone Safari / Recast webapp
  -> getUserMedia camera preview
  -> lightweight JPEG frame POST about every 200ms
  -> Recast Lens Bridge on GN100
  -> latest frame + metadata
  -> object identification via local YOLO
  -> latest-frame interpretation via local NVIDIA Cosmos/VSS-side reasoner
  -> future temporal VSS adapter
```

This is a v1 proof path. It proves Recast-owned iPhone camera ingestion without relying on Larix as submitted product code.

## Run

Run the bridge on the GN100:

```bash
cd ~/recast
python3 services/recast-lens-bridge/server.py --port 8910
```

Run the HTTPS frontend/proxy on the GN100 too. The phone should never need to
call the raw `http://172.16.94.151:8910` bridge directly; it should call the
GN100 HTTPS frontend and let that process forward same-origin Lens requests to
`127.0.0.1:8910`:

```text
iPhone Safari
  -> https://172.16.94.151:5173
  -> /api/recast-lens/*
  -> http://127.0.0.1:8910
```

Mac mini hosting is a debugging fallback only. It is not the intended demo
relay, because it puts the phone, relay, and GN100 bridge on separate machines.

Start the GN100 HTTPS frontend/proxy from `apps/recast-frontend`:

```bash
cd ~/recast/apps/recast-frontend
npm install
npm run build

tmux new-session -d -s recast-frontend-https \
  'cd ~/recast/apps/recast-frontend && \
   python3 tools/serve_https.py \
     --host 0.0.0.0 \
     --port 5173 \
     --dist dist \
     --cert .local/certs/recast-lan.crt \
     --key .local/certs/recast-lan.key \
     --bridge-host 127.0.0.1 \
     --bridge-port 8910'
```

Browser-camera note:

iPhone Safari may require a secure context for `getUserMedia`. If the camera does not open from the LAN HTTP URL, serve the frontend over HTTPS or use a trusted/tunneled development origin for the phone while keeping the bridge on the GN100 LAN.

If the frontend is served over HTTPS, do not point browser fetches directly at
`http://172.16.94.151:8910`; Safari may block that as mixed content. The
frontend HTTPS helper supports a same-origin proxy. With that setup, the phone
calls `/api/recast-lens/frame` on the HTTPS frontend origin, and the helper
forwards to this bridge.

## Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/health` | `GET` | Health check |
| `/viewer` | `GET` | Simple live browser viewer for the latest frame |
| `/api/recast-lens/frame` | `POST` | Accept one JPEG frame |
| `/api/recast-lens/sessions` | `GET` | List active iPhone/browser sessions |
| `/api/recast-lens/status` | `GET` | Return latest session/frame metadata |
| `/api/recast-lens/latest.jpg` | `GET` | Return latest JPEG frame |
| `/api/recast-lens/interpret` | `POST` | Ask local NVIDIA vision reasoner to describe the latest frame |
| `/api/recast-lens/interpretation` | `GET` | Return latest interpretation |
| `/api/recast-lens/detect-objects` | `POST` | Run local YOLO object detection on the latest frame |
| `/api/recast-lens/objects` | `GET` | Return latest object detection result |
| `/api/recast-lens/tracking` | `GET`/`POST` | Read or toggle bridge-owned live object tracking |

Most read/action endpoints accept `?session=<id>` so multiple iPhones can
stream at once without overwriting each other's status, latest frame,
interpretation, object detections, or tracking state. The browser capture client
sends `X-Recast-Session` with every frame upload.

## View On The GN100

Open this on the GN100 desktop to see the incoming phone frames:

```text
http://127.0.0.1:8910/viewer
```

From another LAN machine:

```text
http://172.16.94.151:8910/viewer
```

The viewer refreshes the latest JPEG frame about five times per second. It is a
display/debug view plus latest-frame object detection and "What am I seeing?"
interpretation.

Current performance profile:

```text
frontend frame capture: ~200ms
frontend frame width: 640px
frontend JPEG quality: 0.55
viewer refresh: ~200ms
object detection: ~5000ms, non-overlapping and globally locked
```

The viewer marks frames as `stale` when no new iPhone frame has arrived for
more than five seconds. If the viewer is stale, restart the phone stream before
debugging object detection.

The live image refresh and AI overlay are intentionally separate loops. Video
should keep moving even when object detection is still thinking.

The bridge owns live object tracking. Browser tabs call
`/api/recast-lens/tracking` to enable or disable the bridge-owned tracker, then
poll `/api/recast-lens/status` for cached object boxes. They should not each run
their own repeated `/detect-objects` loop.

The bridge enforces a single object-detection pass at a time and returns cached
results while a pass is running or the latest detection is still fresh. This
protects the GN100 if multiple viewer/capture tabs are open during the demo.

## Vision Interpretation

The bridge can now interpret the latest received iPhone frame:

```bash
curl -X POST http://127.0.0.1:8910/api/recast-lens/interpret \
  -H 'Content-Type: application/json' \
  -d '{"question":"What am I seeing in this Recast Lens frame?"}'
```

This calls the local NVIDIA Cosmos/VSS-side vision reasoner at:

```text
http://127.0.0.1:30082/v1/chat/completions
```

This is not yet full VSS temporal search or clip retrieval. It answers the
first demo question: "what am I seeing right now?" from the latest Recast Lens
frame.

## Object Identification

The bridge can also run local YOLO object detection on the latest frame:

```bash
curl -X POST http://127.0.0.1:8910/api/recast-lens/detect-objects
```

Default runtime on the GN100:

```text
Python: /home/acer01/arlo-vision/bin/python
Model:  /home/acer01/arlo-vision/yolo11m.pt
```

This returns structured labels, confidence, and bounding boxes. It is the right
path for concrete object questions such as whether the frame contains a person
or chair. Distance estimation is intentionally not claimed here.

## Current Limitation

This bridge receives frame-streamed JPEGs, not H.264 RTMP/RTSP video.

That is intentional for the first no-Larix proof. A later Recast Lens native app or WebRTC/WHIP path can replace this transport while keeping the same Recast evidence model.

The next performance step should replace JPEG polling with a real live-video
transport, then keep YOLO/VSS/Cosmos as slower sampled-frame evidence lanes.
