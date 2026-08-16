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
  -> latest frame/status for future VSS adapter
```

This avoids Larix code and avoids the occupied `8099` prototype.

If iPhone Safari refuses camera permission from a LAN HTTP URL, serve the frontend over HTTPS or use a trusted/tunneled development origin. Browser camera APIs can require a secure context even when the bridge itself is reachable on the LAN.
