---
title: ClassificationPrimitive throws when batched instances have different colours - use GroundPrimitive
date: 2026-08-15
category: runtime-errors
module: city-view-3d
problem_type: runtime_error
component: frontend
symptoms:
  - "Cesium dialog: An error occurred while rendering. Rendering has stopped."
  - "DeveloperError: All GeometryInstances must have the same color attribute except via GroundPrimitives"
  - "Painted footprints never appear; scene stops rendering after the primitive is added"
root_cause: wrong_api
resolution_type: code_fix
severity: high
framework_version: "cesium 1.128"
tags: [cesium, classification-primitive, ground-primitive, developer-error, per-instance-color]
---

# ClassificationPrimitive throws when batched instances have different colours - use GroundPrimitive

## Problem

Building footprints were batched into one `Cesium.ClassificationPrimitive` with a different colour per instance (colour
encodes share leased). On first render Cesium threw and stopped rendering.

## Symptoms

- "DeveloperError: All GeometryInstances must have the same color attribute except via GroundPrimitives"
- Whole scene frozen behind the Cesium error dialog.

## What Didn't Work

- Explicit `height`/`extrudedHeight` on the polygons made no difference; the check is about colours, not geometry.

## Solution

Construct the same instances with `Cesium.GroundPrimitive` instead:

```js
new Cesium.GroundPrimitive({
  geometryInstances: instances,            // PolygonGeometry per building, no height/extrudedHeight needed
  classificationType: Cesium.ClassificationType.CESIUM_3D_TILE,
  asynchronous: false
});
```

Per-instance `color` and `show` attributes work as before; `getGeometryInstanceAttributes(id)` still updates them.

## Why This Works

`ClassificationPrimitive.update` (packages/engine/Source/Scene/ClassificationPrimitive.js) throws unless all colours are
equal **or** the instances carry spherical/planar extents attributes. Only `GroundPrimitive` adds those attributes,
which the shadow-volume fragment shader needs to cull each instance's colour to its own extents; without them batched
colours would bleed into each other. `GroundPrimitive` also computes the volume height range itself.

## Prevention

- Reach for `GroundPrimitive` whenever classification needs more than one colour in a batch; use
  `ClassificationPrimitive` only for a single-colour volume you size yourself.
- The `show` attribute is supported by both (`Primitive._appendShowToShader` is applied to the classification shaders).

## Related Issues

- `docs/solutions/architecture-patterns/highlight-buildings-on-google-photorealistic-3d-tiles.md`
