# recast-ios Recon

Status: planning/recon only. No app code has been written in this repo yet.

Compound Engineering plan:

[`../plans/recast-lens-compound-engineering-plan.md`](../plans/recast-lens-compound-engineering-plan.md)

## Goal

Create a Recast-native iPhone app that turns a building walkthrough into structured physical evidence for NVIDIA VSS and the Recast building intelligence layer.

The app should not be a Larix clone. It should be a building evidence capture tool.

## Product Workflow

```text
Choose building
  -> start walkthrough
  -> stream H.264 video-only feed
  -> attach metadata
  -> VSS analyzes the physical asset
  -> Recast links visual evidence to As-Is and Recast Opportunity intelligence
```

## Open-Source Leverage Strategy

Do not blindly import an old repo as the product.

Use open-source projects as compound engineering references:

1. Study how they solve capture, encoding, packetization, reconnect, and stream-health reporting.
2. Spike the smallest possible publisher path.
3. Build Recast-specific product UX and evidence metadata around the proven streaming spine.

## Current Candidate References

| Project | Role | Current Judgment |
| --- | --- | --- |
| HaishinKit.swift | iOS/macOS/tvOS/visionOS camera and microphone streaming via RTMP/SRT | Best candidate to analyze and spike first if freshness and license checks pass |
| RootEncoder-iOS | Swift RTMP/RTSP/SRT encoder | Useful reference, but avoid direct dependency unless freshness/stability improves |
| MediaMTX | Server-side stream broker | Keep as the broker between mobile push and Acer/VSS pull |
| Larix Broadcaster | Behavior reference | Useful proven UX/reference, not a fork target |

## Recommended MVP

```text
SwiftUI shell
  -> camera preview
  -> H.264 video-only publisher
  -> MediaMTX target config
  -> connection status
  -> local recording fallback
  -> building/session metadata
```

Keep MVP small:

- no audio;
- no public streaming destinations;
- no generic broadcaster UI;
- no overlay editor;
- no account-heavy workflow unless required for demo;
- no on-device AI requirement.

## V1 Network Note

Another team member's early prototype is believed to be using the Acer/GN100 host at `172.16.94.151` with port `8099`.

For Recast Lens v1, do not assume `8099` is available. Confirm the running services before binding or publishing to that port. Prefer a Recast-specific MediaMTX path such as `recast-lens-v1` on the established broker, or choose a separate port only after checking for conflicts.

## Core Interfaces To Own

The Recast app should own these concepts even if a streaming library supplies the transport:

- `CaptureSession`
- `StreamPublisher`
- `EvidenceSession`
- `BuildingContext`
- `ConnectionHealth`
- `LocalRecordingFallback`

This makes the streaming engine swappable.

## First Spike Questions

1. Can the app publish H.264 video-only to MediaMTX?
2. Can MediaMTX expose the stream as RTSP for Acer/VSS?
3. Can VIOS register the stream successfully?
4. Can the app reconnect after Wi-Fi drop?
5. Can it record a local fallback clip while streaming?
6. Can it send a building/session metadata packet to Recast?
7. Can VSS return a useful timestamped visual answer?

## Product Name Direction

Working name: **Recast Lens**.

Reason: the iPhone becomes the lens for Recast, while NVIDIA VSS gives Recast eyes.
