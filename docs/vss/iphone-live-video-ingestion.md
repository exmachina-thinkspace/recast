# iPhone Live Video Ingestion

Status: verified as an ingestion path on 2026-08-15.

This document captures the judge-facing architecture and current truth. Detailed internal troubleshooting notes remain in the prep repository.

## What Was Proven

An iPhone can act as a mobile building-capture camera for Recast.

Verified path:

```text
iPhone camera app
  -> H.264 video-only live push
  -> MediaMTX broker
  -> RTSP stream
  -> Acer GN100
  -> VIOS sensor registration
  -> live computer-vision consumption
```

The successful prototype used a Larix-style iPhone broadcaster path and MediaMTX as the stream broker. MediaMTX accepts the phone's push stream and exposes a pullable RTSP stream for the Acer/VSS side.

## Why It Matters

This gives Recast a practical mobile physical-evidence path:

```text
Select building
  -> walk the asset with a phone
  -> stream physical evidence to the Acer GN100
  -> attach VSS observations to the building intelligence record
```

The iPhone path is more useful than a fixed camera for a building walkthrough because the operator can move through lobbies, vacant areas, loading zones, mechanical rooms, corridors, and floorplates.

## Current Technical Shape

| Layer | Current Direction |
| --- | --- |
| Mobile capture | H.264, video-only |
| Stream push | RTMP or equivalent mobile push |
| Broker | MediaMTX |
| Acer/VSS input | RTSP pull |
| Demo display | WebRTC or VSS UI |
| Fallback | record a clip, then send it through VSS as a file |

Audio should remain off unless explicitly tested. The verified path avoided audio because audio tracks introduced compatibility problems in VIOS registration.

Networking note:

Another team member's early prototype is believed to be using `172.16.94.151:8099` on the Acer/GN100 network. Treat port `8099` as potentially occupied until verified. Recast Lens should avoid binding to that port by default and should prefer a distinct stream path such as `recast-lens-v1` on the established broker.

Recast-owned v1 note:

The first Recast-owned implementation path is browser-camera frame streaming, not Larix and not H.264 RTMP/RTSP yet:

```text
iPhone browser camera
  -> Recast frontend
  -> Recast Lens bridge on port 8910
  -> latest JPEG frame/status
  -> future VSS adapter
```

## What Is Not Proven Yet

The ingestion path is proven. The full "Ctrl-F for a building" VSS experience is not yet proven.

Still gated:

- live VSS captioning from the iPhone stream;
- natural-language query over the live stream;
- timestamped clip retrieval from a walkthrough;
- latency acceptable for the judge-facing moment;
- robust venue-network behavior.

## Acceptance Test

For Demo 1, the important test is not "can the phone stream video?"

The important test is:

> Can Recast ask a question about a physical asset and have NVIDIA VSS retrieve useful visual evidence?

Minimum useful queries:

- Show me underutilized areas.
- Find large open spaces.
- Show me loading or service access.
- Find physical features relevant to converting this space to another use.
- Show me evidence that supports or contradicts our hypothesis about this space.

Each test should record:

- query;
- result;
- timestamp or clip returned;
- correctness;
- latency;
- usefulness for Recast.

## Product Direction

The long-term mobile product should not be a generic live-streaming app. It should be `recast-ios`: a building evidence capture tool that adds context to the video stream.

Needed app context:

- building;
- floor / zone / room;
- walkthrough session;
- operator notes;
- hypothesis being tested;
- timestamp;
- location/heading when useful and privacy-safe;
- link to the Recast evidence packet.
