# Recast Frontend

This is the judge-facing Recast webapp.

It is separate from the future Recast Lens iOS app:

- webapp = Recast intelligence, story, evidence display, agent workflow;
- Recast Lens = native building evidence capture.

## Compound Engineering Plan

Use the webapp plan before broad implementation:

[`../../docs/plans/recast-webapp-compound-engineering-plan.md`](../../docs/plans/recast-webapp-compound-engineering-plan.md)

## Current Runtime Shape

The frontend is a React/Vite app that consumes existing local services.

Current client assumptions in `src/api.js`:

- buildings API: port `8900`;
- agent API: port `8601`;
- Recast Lens bridge: port `8910`;
- 3D city view: port `8700`;
- host is derived from `window.location.hostname`.

These assumptions can be overridden with Vite environment variables. Start with:

```bash
cp .env.example .env.local
```

Important Recast Lens capture note: if the frontend is served over HTTPS, leave
`VITE_LENS_BRIDGE_URL` unset and use `LENS_BRIDGE_TARGET` instead. The browser
will call same-origin `/api/recast-lens/*`, and Vite will proxy to the GN100
bridge server-side. That avoids Safari mixed-content blocking.

## Capture Boundary

Do not make this webapp a Larix clone.

Larix or similar tools may be used as behavior references or temporary test signal sources, but they should not be submitted as Recast code or represented as the Recast product.

The webapp should display NVIDIA VSS evidence through a Recast-owned backend/VSS adapter:

```text
webapp
  -> Recast backend / VSS adapter
  -> NVIDIA VSS on Acer GN100
  -> timestamped visual evidence
  -> Recast evidence model
  -> webapp display
```

VSS is the visual-understanding engine. Recast is the workflow, data, and recommendation layer around it.

## Recast Lens V1

The `CaptureScreen` now includes a Recast-owned browser-camera path:

```text
iPhone browser camera
  -> Recast frontend
  -> POST JPEG frames to Recast Lens bridge on port 8910
  -> latest frame/status
  -> structured object identification via local YOLO
  -> latest-frame interpretation via local NVIDIA Cosmos/VSS-side reasoner
```

This avoids Larix code and avoids the occupied `8099` prototype.

The capture screen includes a `What am I seeing?` button. It asks the GN100
bridge to interpret the latest received frame using the local NVIDIA vision
reasoner. This is deliberately narrower than full VSS temporal search: it
answers the current-frame demo question first.

The capture screen also includes `Identify objects`, which runs local YOLO on
the latest frame and returns structured objects such as `person`, `chair`, and
other COCO labels with confidence and bounding boxes. It does not estimate
distance.

If iPhone Safari refuses camera permission from a LAN HTTP URL, serve the frontend over HTTPS or use a trusted/tunneled development origin. Browser camera APIs can require a secure context even when the bridge itself is reachable on the LAN.

Known failure mode:

```text
http://172.16.94.151:5173/?step=capture
```

The page can display from that URL, but `Start camera` is expected to fail on
iPhone Safari because LAN HTTP is not a secure browser origin for
`getUserMedia`.

## GN100 Camera Demo Host

The Recast Lens demo should be hosted from the GN100, not from a developer Mac.
The iPhone should talk to one HTTPS origin on the GN100, and that frontend
process should proxy Lens API calls to the GN100-local bridge:

```text
iPhone Safari
  -> https://172.16.94.151:5173
  -> same-origin /api/recast-lens/*
  -> http://127.0.0.1:8910 on the GN100
```

Do not use the Mac mini as the relay for the judge/demo path. A Mac-hosted
frontend is acceptable only as a temporary debugging fallback when GN100 shell
access is unavailable.

For the iPhone camera button to work, the frontend must be opened from a secure
origin such as HTTPS. On the GN100, create a self-signed cert for the GN100 LAN
IP:

```bash
cd ~/recast/apps/recast-frontend
mkdir -p .local/certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout .local/certs/recast-lan.key \
  -out .local/certs/recast-lan.crt \
  -days 7 \
  -subj "/CN=172.16.94.151" \
  -addext "subjectAltName=IP:172.16.94.151,DNS:localhost"
```

Start or verify the GN100 bridge:

```bash
cd ~/recast
python3 services/recast-lens-bridge/server.py --port 8910
```

For a stable demo process, prefer the built frontend plus the HTTPS proxy helper
instead of a detached Vite dev server:

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

Then open on the iPhone:

```text
https://172.16.94.151:5173/?step=capture
```

The iPhone may need to trust the certificate before Safari treats the origin as
secure.

Smoke-test the same-origin proxy:

```bash
curl -k https://172.16.94.151:5173/health
```
