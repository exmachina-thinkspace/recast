# Recast Lens Compound Engineering Plan

Status: planning pass before app creation.

Last updated: 2026-08-16.

No `recast-ios` app code has been written from this plan yet.

## Compound Engineering Frame

This plan applies the Compound Engineering loop from the prep repo reference:

```text
brainstorm -> plan -> work -> simplify -> review -> compound
```

Reference:

`/Users/peterchee/.openclaw/workspace-dev-ava/city-of-seattle-prep/external-repos/compound-engineering.md`

Use Compound Engineering here as a workflow discipline, not as product code or a dependency.

## Brainstorm

### Product Thesis

Recast Lens is not a Larix clone.

It is a Recast-native iPhone app that turns a building walkthrough into structured physical evidence for NVIDIA VSS and the Recast building intelligence layer.

The phone is the lens. NVIDIA VSS gives Recast eyes. The Recast data layer gives the video context.

### User Job

An operator should be able to walk a building and produce evidence that helps answer:

1. What physical condition/use does this building appear to have?
2. What spaces are underused, empty, constrained, or flexible?
3. What visual evidence supports or contradicts a reuse hypothesis?
4. What should Recast attach back to the building record?

### Demo Job

For Demo 1, Recast Lens only needs to support the VSS proof moment:

```text
choose building
  -> start walkthrough
  -> stream H.264 video-only feed
  -> MediaMTX
  -> RTSP pull on Acer GN100 / VSS side
  -> ask building-search questions
  -> return timestamped physical evidence
```

The judge-facing magic is not "the phone streams video."

The judge-facing magic is:

> Recast can ask a question about a physical asset and NVIDIA can retrieve useful visual evidence.

## Plan

### MVP Scope

Build the smallest native iOS app that can prove Recast-owned capture:

- SwiftUI shell.
- Camera preview.
- H.264 video-only publisher.
- MediaMTX target configuration.
- Start/stop walkthrough.
- Connection status.
- Local recording fallback.
- Building/session metadata packet.

Keep out of MVP:

- audio;
- generic public streaming destinations;
- social/live-broadcast UI;
- overlay editor;
- account-heavy onboarding;
- on-device AI;
- full inspection checklist product;
- app-store polish.

### References To Study

Do not copy or vendor old broadcaster apps directly.

Use references to learn implementation patterns:

| Reference | Use | Boundary |
| --- | --- | --- |
| Larix Broadcaster | Behavior and operator UX reference | Do not fork or clone product surface |
| HaishinKit.swift | Candidate streaming spine for RTMP/SRT-style publish | Use only after license/freshness check |
| RootEncoder-iOS | Encoder/transport reference | Reference only unless freshness and maintenance improve |
| MediaMTX | Stream broker between phone and Acer/VSS | Keep as explicit infrastructure dependency |

Open-source leverage rule:

Study capture, encoding, packetization, reconnect, health, and stream fallback patterns. Own Recast product concepts and evidence metadata ourselves.

### Recast-Owned Interfaces

The app should have clean boundaries even if the streaming library changes:

- `CaptureSession`
- `StreamPublisher`
- `EvidenceSession`
- `BuildingContext`
- `ConnectionHealth`
- `LocalRecordingFallback`

Transport is replaceable. Evidence semantics are Recast-owned.

### Metadata Contract

Every walkthrough should be able to produce a small metadata packet:

```json
{
  "building_id": "string",
  "building_label": "string",
  "walkthrough_session_id": "string",
  "operator_label": "string",
  "floor": "string",
  "zone": "string",
  "hypothesis": "string",
  "stream_url": "string",
  "started_at": "timestamp",
  "recording_fallback_path": "string_or_null"
}
```

Do not make this a permanent API contract yet. Treat it as the minimum evidence context needed to connect the phone stream to Recast and VSS.

## Work

### Spike 1 - Streaming Spine

Goal:

Prove a native app can publish H.264 video-only to MediaMTX.

Acceptance:

- camera preview renders;
- stream starts/stops;
- MediaMTX receives the stream;
- stream can be pulled as RTSP;
- no audio track is emitted;
- connection status is visible to the operator.

Network constraint:

Another team member's early prototype is believed to be using `172.16.94.151:8099`. Treat port `8099` as potentially occupied. Do not bind Recast Lens v1 to that port unless the team confirms the prototype is stopped or that `8099` is the intended shared endpoint.

### Spike 2 - Acer / VSS Registration

Goal:

Prove the phone-origin stream can be registered on the Acer/VSS side.

Acceptance:

- RTSP URL is reachable from Acer/GN100 network;
- VIOS or VSS registration succeeds;
- live frame consumption is visible;
- failure mode is documented if live VSS cannot register it.

### Spike 3 - Evidence Metadata

Goal:

Attach building/session context to the stream.

Acceptance:

- operator can select or enter a building;
- session ID is generated;
- stream URL and metadata are emitted together;
- metadata can be posted to or saved for the Recast app layer;
- no credentials are hardcoded.

### Spike 4 - Fallback Recording

Goal:

If live VSS is unstable, still produce a usable clip.

Acceptance:

- local video recording is created while or after streaming;
- recording can be exported or handed to VSS as file input;
- UI labels fallback evidence as recorded, not live.

### Spike 5 - Ctrl-F Proof

Goal:

Answer whether Recast Lens plus VSS can retrieve useful physical evidence.

Required queries:

- Show me empty or underutilized areas.
- Find large open spaces.
- Show loading or service access.
- Find physical features relevant to converting this space to another use.
- Show evidence that supports or contradicts our hypothesis about this space.

Acceptance:

- at least three queries return relevant timestamps or clips;
- latency is recorded;
- correctness is judged manually;
- weak or failed queries are preserved;
- unsupported claims are marked `INSUFFICIENT_EVIDENCE`.

## Simplify

Before broadening the app, cut anything that does not serve the proof moment.

Default simplifications:

- one building/session at a time;
- one configured MediaMTX target;
- one Recast-specific stream path, such as `recast-lens-v1`;
- one video profile first: 720p, 30 FPS, H.264, video-only;
- no background upload queue until fallback files are proven useful;
- no polished account model until the demo needs more than one operator;
- no map inside the app until the web app needs mobile-driven building selection.

## Review

Review the first implementation against this plan, not against Larix feature parity.

Key review questions:

1. Can a teammate start the app and capture a walkthrough without engineering help?
2. Is the stream stable enough for Demo 1?
3. Can Acer/VSS consume the stream or fallback clip?
4. Does every visual observation have building/session context?
5. Are failures honest and visible?
6. Are secrets outside Git?
7. Is the streaming engine isolated behind Recast-owned interfaces?

## Compound

After each spike, update durable docs:

- `docs/mobile/recast-ios-recon.md` for product/mobile direction.
- `docs/vss/iphone-live-video-ingestion.md` for ingestion truth.
- `docs/demo1/05-vss-gn100-plan.md` for VSS gating status.
- `docs/solutions/` for reusable implementation lessons.

Do not leave learnings only in terminal history or chat.

## Go / No-Go Before Coding

Proceed to app creation only if the team agrees:

- Recast Lens is a building evidence tool, not a generic broadcaster.
- MVP is video-only H.264 streaming plus metadata and fallback recording.
- MediaMTX remains the broker for the first implementation.
- The first hard gate is VSS usefulness, not iOS UI polish.
- Existing open-source projects are references or dependencies after review, not products to copy wholesale.

## Current Recommendation

Start with a minimal native SwiftUI app and evaluate HaishinKit.swift as the first streaming implementation candidate, subject to freshness/license review.

If the native streaming library path slows the team down, use the proven Larix-style external app for the hackathon demo while building Recast Lens metadata and workflow around recorded/walkthrough sessions.

That fallback still preserves the core Recast story:

```text
building context + mobile physical evidence + NVIDIA VSS + Recast assessment
```
