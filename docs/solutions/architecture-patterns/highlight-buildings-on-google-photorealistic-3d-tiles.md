---
title: Highlight individual buildings on Google Photorealistic 3D Tiles with classification volumes
date: 2026-08-15
category: architecture-patterns
module: city-view-3d
problem_type: architecture_pattern
component: frontend
severity: high
applies_when:
  - "Rendering Google Photorealistic 3D Tiles (or any fused photogrammetry mesh) in CesiumJS and needing per-building highlight, selection or colour coding"
  - "A stakeholder asks to 'select the building' or 'colour that building' on a photorealistic city"
tags: [cesium, google-3d-tiles, classification, ground-primitive, footprints, highlight]
---

# Highlight individual buildings on Google Photorealistic 3D Tiles with classification volumes

## Context

Google's Photorealistic 3D Tiles are one fused mesh: there are no per-building objects, feature IDs or batch tables to
select or recolour. The first version of the citywide view therefore showed a dot per building; the team wanted the
buildings themselves highlighted in colour.

## Guidance

Paint the mesh instead of selecting an object. For each building, take a ground footprint polygon and turn it into a
**classification volume**: a `Cesium.GroundPrimitive` whose geometry instance is a `PolygonGeometry` of the footprint,
with `classificationType: Cesium.ClassificationType.CESIUM_3D_TILE`. Wherever the tile mesh falls inside that volume
(roof, every facade), the fragment is tinted with the instance colour. Batch all buildings into one primitive with
per-instance `ColorGeometryInstanceAttribute` and `ShowGeometryInstanceAttribute`; update colours/visibility later via
`primitive.getGeometryInstanceAttributes(id)` once `primitive.ready`.

Details that matter:

- `GroundPrimitive` sizes the volume from Cesium's approximate terrain heights for the region (Seattle's tile spans
  roughly −4.5 km to +4.4 km), so towers are fully covered without computing per-building heights.
- Buffer footprints ~2 m outward before use. Photogrammetry walls bulge past surveyed outlines and upper floors can
  overhang the ground floor; unbuffered footprints leave facade strips unpainted (seen on Fourth & Battery).
- Semi-transparent colour (alpha ≈ 0.6–0.7) keeps the building's texture readable; the selected building can go to
  ≈0.95 in a distinct hue, with everything else stepped back.
- Desaturate and dim the tileset with a `CustomShader` (`material.diffuse = mix(bg, mix(c, vec3(lum), gray), dim)`)
  so the paint is the loudest thing on screen; a uniform toggles it live.
- Do not use the classification primitive's pick id for interaction — see
  `docs/solutions/ui-bugs/cesium-shadow-volume-pick-returns-wrong-building.md`.
- Per-instance colours are only legal through `GroundPrimitive` — see
  `docs/solutions/runtime-errors/classification-primitive-per-instance-colors-require-ground-primitive.md`.

## Why This Matters

It is the only way to make "this building" light up on Google's tiles without replacing them with abstract extrusions,
and it costs one batched draw regardless of building count. Everything else (selection ring, dimming, colour by score)
builds on it.

## When to Apply

- Any Cesium view over Google Photorealistic 3D Tiles that must colour or select buildings.
- Not needed for tilesets that already carry per-feature IDs (Cesium OSM Buildings, own b3dm/glTF with batch tables) —
  style those with `Cesium3DTileStyle` / feature picking instead.

## Examples

`apps/city-view-3d/seattle-office-vitals-3d.html`: `buildFootprints()` builds the batched `GroundPrimitive`,
`fpColor()` decides colours, `syncFootprints()` pushes attribute changes, `grayShader` dims the tiles.

## Related

- `docs/solutions/best-practices/osm-footprint-matching-for-geocoded-addresses.md`
- `docs/plans/2026-08-15-1644-feat-city-view-3d-plan.md`
