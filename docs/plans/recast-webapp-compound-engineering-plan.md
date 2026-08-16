# Recast Webapp Compound Engineering Plan

Status: planning pass for the judge-facing webapp.

Last updated: 2026-08-16.

This is separate from the Recast Lens iOS plan. The webapp is the judge-facing Recast experience. Recast Lens is a future/native capture instrument.

## Compound Engineering Frame

This plan applies the same workflow discipline:

```text
brainstorm -> plan -> work -> simplify -> review -> compound
```

Reference:

`/Users/peterchee/.openclaw/workspace-dev-ava/city-of-seattle-prep/external-repos/compound-engineering.md`

Use Compound Engineering as the operating model for the work. Do not add Compound Engineering as product code or a runtime dependency.

## Brainstorm

### Product Thesis

The webapp should make the Recast thesis obvious in two to three minutes:

> Recast understands what a building is today, where it is heading, what it could become, and what could make that transformation economically possible.

The webapp is the control tower:

```text
city-scale opportunity layer
  -> building As-Is / trajectory
  -> physical evidence from VSS
  -> alternative futures
  -> capital-stack / incentive fit
  -> best path forward
```

### User Job

The judge or operator should be able to:

1. See that Recast finds buildings worth attention.
2. Pick a building.
3. Understand why it is vulnerable/distressed/opportunity-rich.
4. See what physical evidence NVIDIA VSS adds.
5. Compare plausible futures.
6. See what could make the future economically possible.
7. Ask the agent follow-up questions.

### Existing Starting Point

The current app lives at:

`apps/recast-frontend/`

Current screens:

- `MapScreen`
- `ScoreScreen`
- `CaptureScreen`
- `PossibilityScreen`
- `UpdatedScoreScreen`
- `ChatScreen`

Current API shape:

- buildings API on port `8900`;
- agent API on port `8601`;
- Recast Lens bridge on port `8910`;
- city 3D view on port `8700`;
- frontend derives host from `window.location.hostname`.

The current capture screen is not an iOS app. It is a webapp screen that calls backend services for photo analysis, camera description, voice transcription, agent chat, and Recast-owned browser-camera frame streaming.

## Plan

### Webapp Role

The webapp should not try to be a native broadcaster.

Its role:

- present the Recast story;
- call Recast APIs;
- display VSS evidence and evidence limitations;
- connect a selected building to underlying data;
- show the recommendation path.

It can start or reference a capture session, but it should not own low-level iPhone H.264 publishing in v1.

### MVP Scope

Build around the existing React/Vite app:

- city/opportunity opening;
- one hero building journey;
- before/after Recast score or assessment;
- VSS evidence panel;
- alternate future panel;
- capital-stack opportunity panel;
- agent chat/follow-up;
- clear labels for `KNOWN`, `OBSERVED`, `INFERRED`, `UNKNOWN`, and `INSUFFICIENT_EVIDENCE`.

Keep out of MVP:

- full account system;
- generalized CRM;
- unrestricted citywide search;
- full financial model;
- verified grant eligibility engine;
- native iPhone capture implementation;
- Larix-derived app code;
- hidden demo-only fake data.

## Larix / Open-Source Boundary

Do not use Larix source code as submitted Recast code.

Peter's concern is correct: if the entry relies on an existing broadcaster app's code as the core mobile capture product, we risk failing the "what did we build" test, and possibly the hackathon eligibility rules depending on their exact wording.

Acceptable roles for Larix:

- behavior reference;
- manual test utility;
- temporary signal generator;
- proof that the network path can work.

Unacceptable roles for Larix:

- fork target;
- submitted app;
- codebase we claim as Recast Lens;
- hidden dependency for the product workflow;
- the thing judges think we built.

For the submitted/demoed Recast product, emphasize Recast-owned code and NVIDIA VSS integration:

```text
Recast webapp + Recast backend + local Recast database + NVIDIA VSS
```

If a non-Recast camera app is used as a temporary input source, label it as a test camera source, not as the Recast product.

## Is It By Using NVIDIA VSS Directly?

Partly, but not from the browser directly.

The clean architecture is:

```text
webapp
  -> Recast backend / VSS adapter
  -> NVIDIA VSS on Acer GN100
  -> timestamped visual evidence
  -> Recast evidence model
  -> webapp display
```

The browser should not hold NVIDIA credentials, database credentials, or direct privileged VSS configuration.

Use NVIDIA VSS directly as the visual-understanding engine. Use Recast-owned backend code as the adapter that registers sources, asks questions, stores/normalizes results, and returns judge-safe evidence to the webapp.

VSS is the engine. Recast is the workflow and intelligence layer around it.

## Work

### Spike 1 - Replace Template README / App Identity

Goal:

Make `apps/recast-frontend/` describe Recast instead of the default Vite template.

Acceptance:

- README explains how to run the app;
- ports are documented;
- app role is clear;
- no secrets are documented.

### Spike 2 - Judge Flow Tightening

Goal:

Make the existing screens map to the Recast pitch:

```text
city -> building -> evidence -> possibility -> updated assessment
```

Acceptance:

- first screen immediately communicates Recast opportunity layer;
- building detail explains As-Is and trajectory;
- capture/evidence screen does not overclaim VSS;
- possibility screen shows 2-3 plausible futures;
- updated screen explains why the recommendation changed.

### Spike 3 - VSS Evidence Adapter

Goal:

Connect webapp evidence display to actual VSS or recorded VSS output.

Acceptance:

- backend exposes evidence endpoint;
- evidence has timestamp/clip reference when available;
- every observation has evidence label and confidence/limitation;
- failures can be shown honestly;
- cached VSS output is clearly labeled if used.

### Spike 3A - Recast Lens Web Capture

Goal:

Prove iPhone browser-camera ingestion without Larix.

Acceptance:

- iPhone browser camera preview works in the Recast frontend;
- frontend posts JPEG frames to Recast Lens bridge on port `8910`;
- bridge stores latest frame/status;
- port `8099` remains untouched;
- docs clearly state this is frame-stream v1, not H.264 RTMP/RTSP yet.

Implementation notes from the first working pass:

- Recast-owned browser capture successfully opened the iPhone camera from an HTTPS frontend.
- Frames reached the GN100 bridge on port `8910`.
- The GN100 viewer displayed incoming frames.
- Latest-frame NVIDIA vision interpretation answered "what am I seeing?"
- Local YOLO object identification found common objects and returned labels, confidence, and boxes.
- Bounding boxes were overlaid on the phone capture screen and GN100 viewer.
- Live tracking was added as a repeated detector pass over the latest frame.

Compound lesson:

Do not let object detection drive the apparent video stream. The video refresh path and AI interpretation path must be separated:

```text
fast path: iPhone frame/video transport -> viewer refresh
slow path: sampled latest frame -> YOLO/VSS/Cosmos -> overlay/evidence
```

The JPEG-frame path is acceptable as a no-Larix proof of ownership, but it is not the final live-video architecture. For the next transport pass, prefer H.264/WebRTC/RTMP via a Recast-owned adapter while continuing to sample frames for VSS/object evidence.

Current v1 performance profile:

- frontend frame capture: about every `200ms`;
- frame width: `640px`;
- JPEG quality: `0.55`;
- viewer refresh: about every `200ms`;
- object detection cadence: about every `5000ms`, non-overlapping in the browser and globally locked on the bridge.

This should make the viewer feel live while keeping AI overlays asynchronous. If the viewer shows `stale`, the phone is not currently uploading frames to the bridge.

Regression note:

Running YOLO from multiple viewer/capture tabs can saturate the GN100 if each tab starts its own detector loop. The bridge must enforce one detector pass at a time and return cached detections while a new pass is running or while the cache is still fresh. Live video refresh is the priority; AI overlays are allowed to lag.

Next-phase separation implemented:

```text
browser/video path:
  iPhone camera -> lightweight JPEG frame posts -> bridge latest.jpg -> viewer refresh

AI path:
  bridge-owned background tracker -> sampled latest frame -> YOLO -> cached object overlay
```

The browser no longer owns repeated YOLO calls for live tracking. Capture and
viewer screens toggle `/api/recast-lens/tracking`, then poll bridge status and
render the latest cached boxes. This prevents multiple tabs from spawning
competing detector loops.

### Spike 4 - Capital Stack Panel

Goal:

Show economic possibility without pretending eligibility is verified.

Acceptance:

- programs are classified as `VERIFIED_ELIGIBLE`, `POTENTIALLY_RELEVANT`, `UNKNOWN`, or `NOT_ELIGIBLE`;
- no program is shown as verified unless source rules support it;
- panel explains what still needs diligence.

### Spike 5 - Agent Chat

Goal:

Let judges ask grounded follow-up questions.

Acceptance:

- agent answers from Recast data/tools;
- tool trace is visible or auditable;
- answer distinguishes known facts from inference;
- no private/review-gated claims are promoted without labels.

## Simplify

Default simplifications for hackathon:

- one primary hero building;
- one backup building;
- one future-proofing contrast if data is ready;
- one VSS source or one recorded walkthrough;
- one capital-stack example;
- one clean story path;
- one fallback mode for failed VSS.

Do not build a general platform UI before the demo story is obvious.

## Review

Review questions:

1. Can a judge understand the problem in 10 seconds?
2. Can a judge understand why the selected building deserves attention?
3. Is NVIDIA VSS visibly doing something records cannot?
4. Does the app show what the building could become?
5. Does the app show what could make the transformation economically possible?
6. Are data claims sourced and caveated?
7. Are private/review-gated signals labeled?
8. Is the app using Recast/NVIDIA capability rather than passing off Larix or another broadcaster as our work?

## Compound

After each webapp pass, update durable docs:

- `apps/recast-frontend/README.md` for run/use instructions;
- `docs/demo1/09-demo-script.md` for judge flow;
- `docs/vss/iphone-live-video-ingestion.md` for VSS/capture truth;
- `docs/solutions/` for reusable implementation learnings.

Keep the webapp plan and Recast Lens plan separate:

- webapp = judge-facing Recast intelligence;
- Recast Lens = native building evidence capture.

## Current Recommendation

Continue the webapp as the primary hackathon demo surface.

Do not try to ship a native iOS app unless the webapp/VSS story is already stable.

For capture, avoid using Larix as submitted product code. For the demo, either:

1. use VSS against a recorded walkthrough or approved live source and make the webapp show the VSS evidence; or
2. use a temporary camera-source tool only as an input generator while making clear that Recast's submitted work is the webapp, backend, data layer, VSS integration, and assessment workflow.

The strongest entry is not "we built a streamer."

The strongest entry is:

```text
We combined city/property/financial data with NVIDIA VSS physical evidence to determine what a distressed building could become.
```
