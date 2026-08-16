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
  -> JPEG frame POST every ~750ms
  -> Recast Lens Bridge on GN100
  -> latest frame + metadata
  -> future VSS adapter
```

This is a v1 proof path. It proves Recast-owned iPhone camera ingestion without relying on Larix as submitted product code.

## Run

On the GN100:

```bash
cd services/recast-lens-bridge
python3 server.py --port 8910
```

Open the frontend from the same GN100 host so the browser derives the same host:

```text
http://172.16.94.151:8800
```

The frontend sends frames to:

```text
http://172.16.94.151:8910/api/recast-lens/frame
```

Browser-camera note:

iPhone Safari may require a secure context for `getUserMedia`. If the camera does not open from the LAN HTTP URL, serve the frontend over HTTPS or use a trusted/tunneled development origin for the phone while keeping the bridge on the GN100 LAN.

If the frontend is served over HTTPS, do not point browser fetches directly at
`http://172.16.94.151:8910`; Safari may block that as mixed content. The
frontend dev server supports a same-origin proxy:

```bash
LENS_BRIDGE_TARGET=http://172.16.94.151:8910 \
HTTPS_KEY=.local/certs/recast-lan.key \
HTTPS_CERT=.local/certs/recast-lan.crt \
npm run dev:lan
```

With that setup, the phone calls `/api/recast-lens/frame` on the HTTPS frontend
origin, and Vite forwards to this bridge.

## Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/health` | `GET` | Health check |
| `/viewer` | `GET` | Simple live browser viewer for the latest frame |
| `/api/recast-lens/frame` | `POST` | Accept one JPEG frame |
| `/api/recast-lens/status` | `GET` | Return latest session/frame metadata |
| `/api/recast-lens/latest.jpg` | `GET` | Return latest JPEG frame |

## View On The GN100

Open this on the GN100 desktop to see the incoming phone frames:

```text
http://127.0.0.1:8910/viewer
```

From another LAN machine:

```text
http://172.16.94.151:8910/viewer
```

The viewer refreshes the latest JPEG frame about twice per second. It is a
display/debug view, not VSS understanding yet.

## Current Limitation

This bridge receives frame-streamed JPEGs, not H.264 RTMP/RTSP video.

That is intentional for the first no-Larix proof. A later Recast Lens native app or WebRTC/WHIP path can replace this transport while keeping the same Recast evidence model.
