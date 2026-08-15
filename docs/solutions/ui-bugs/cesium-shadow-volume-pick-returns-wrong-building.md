---
title: Clicking a painted building selects a neighbour - Cesium shadow-volume picking is unreliable for batched classification
date: 2026-08-15
category: ui-bugs
module: city-view-3d
problem_type: ui_bug
component: frontend
symptoms:
  - "scene.pick(...).id returns a different building's instance id at some pixels of the same painted building"
  - "Clicking Fourth & Vine opened the card for 222 5th Ave N (over 1 km away); other pixels of the same building picked correctly"
  - "Hover highlight flickers between buildings while the cursor stays on one facade"
root_cause: wrong_api
resolution_type: code_fix
severity: high
framework_version: "cesium 1.128"
tags: [cesium, picking, ground-primitive, classification, shadow-volume, point-in-polygon, pickposition]
---

# Clicking a painted building selects a neighbour - Cesium shadow-volume picking is unreliable for batched classification

## Problem

Selection used `viewer.scene.pick(position).id` and mapped the classification instance id back to a building. With
116 tall classification volumes batched in one `GroundPrimitive`, the id was often a neighbour's — sometimes a building
a kilometre away — depending on which pixel of the building was clicked.

## Symptoms

- Same building, different pixels → different ids (`fourthVine_120_540: f46` but `fourthVine_90_560: f33`).
- Wrong card opens; correct paint (rendering was never wrong, only picking).

## What Didn't Work

- Assuming the extents culling that fixes rendering also fixes picking. It does not.

## Solution

Stop trusting the pick id for footprints. Resolve the mesh point under the cursor and hit-test it against the same
polygons that drive the paint:

```js
function footprintAt(pos){
  const c = viewer.scene.pickPosition(pos);            // exact mesh point on the Google tiles
  if (!c) return null;
  const g = Cesium.Cartographic.fromCartesian(c);
  const lon = Cesium.Math.toDegrees(g.longitude), lat = Cesium.Math.toDegrees(g.latitude);
  return footprints.find(f => inBbox(lon, lat, f) && pointInPolygon(lon, lat, f.p)) || null;
}
function pickBuilding(pos){
  const p = viewer.scene.pick(pos);                    // still fine for entities (pins/labels)
  if (p && p.id && p.id.b) return p.id.b;
  return footprintAt(pos);
}
```

Throttle the hover path (~60 ms) since `pickPosition` renders a depth pass per call. Requires `scene.pickPositionSupported`
(depth textures — true in every current browser).

## Why This Works

In `ShadowVolumeAppearanceFS.glsl` the `PICK` branch reads
`if (0.0 <= uv.x && uv.x <= 1.0 && 0.0 <= uv.y && uv.y <= 1.0 || logDepthOrDepth != 0.0)` — the `||` means the extents
test never culls in the pick pass, so every volume whose faces cover the pixel writes its pick colour and the last one
wins. The colour pass uses a proper `discard`, which is why rendering is correct. Hit-testing the actual mesh position
against the footprint polygons is exact by construction: "inside the polygon" is precisely the condition that painted
the pixel.

## Prevention

- Treat classification / ground primitive pick ids as unreliable whenever many volumes overlap in screen space (tall
  volumes always do). Prefer `pickPosition` + geometry tests.
- Probe several pixels of one object when validating picking; a single hit can be right by accident.

## Related Issues

- `docs/solutions/architecture-patterns/highlight-buildings-on-google-photorealistic-3d-tiles.md`
