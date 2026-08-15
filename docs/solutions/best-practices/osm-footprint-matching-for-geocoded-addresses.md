---
title: Matching geocoded building addresses to OpenStreetMap footprints
date: 2026-08-15
category: best-practices
module: city-view-3d
problem_type: best_practice
component: data_model
severity: medium
applies_when:
  - "You have a list of buildings as address + lat/lon and need polygons (footprints) for painting, extrusion or joins"
  - "Overpass API queries time out (504) or a fraction of points fall just outside their building outline"
tags: [openstreetmap, overpass, footprints, point-in-polygon, geocoding, seattle, odbl]
---

# Matching geocoded building addresses to OpenStreetMap footprints

## Context

The citywide view had 124 office buildings as geocoded addresses. Painting them on the Google mesh needed a footprint
polygon per building. Downtown Seattle is well mapped in OSM, but geocodes land on sidewalks, courtyards and party
walls, and Overpass is easy to overload.

## Guidance

1. **Query one bounding box, not one `around` per point.** 248 `around:` clauses returned HTTP 504 from three Overpass
   mirrors; a single `[bbox:S,W,N,E];(way["building"];relation["building"];);out tags geom;` for the whole area
   (13.5k elements, ~12 MB) returned in seconds. Cache the raw response on disk so re-runs are offline.
2. **Match in tiers, and record which tier fired.** (a) point-in-polygon — if several polygons contain the point, take
   the smallest (a tower part rather than a whole-block outline); (b) else nearest polygon edge within a tight radius
   (15 m; at 40 m you start painting the neighbour across the alley); (c) else a hand-checked override by OSM id;
   (d) else no footprint — fall back to a marker and say so in the UI. Handle multipolygon relations by their `outer`
   rings.
3. **Audit by name.** Print the OSM `name` next to your name for every match; wrong neighbours jump out (a hotel for a
   development site, a parking garage for the Colman Building) and rescues become obvious (JLL "2101–2121 4th Ave" is
   OSM "Fourth and Blanchard Building").
4. **Buffer the rings ~2 m outward** for photogrammetry use (miter offset, clamped) — see the classification pattern doc.
5. **Keep the attribution.** OSM data is ODbL: "Footprints © OpenStreetMap contributors" stays on screen.
6. **Embed lean.** Round to 6 decimals, keep only ring + a few tags (name, levels, height, OSM id) in the page; keep the
   full match audit (`how`, distances) in a sidecar JSON.

## Why This Matters

Footprint accuracy is the difference between "the building lights up" and "the parking lot next door lights up". The
tiered match plus the name audit found 116/124 with confidence and correctly left 8 unbuilt/proposed sites as pins
rather than painting whatever currently occupies the lot.

## When to Apply

- Building-level visualisation from address lists (availability reports, permit lists, benchmarking exports).
- Joining an address list to a parcel/building spine when no parcel id is present.

## Examples

`apps/city-view-3d/tools/build_footprints.py` implements the whole pipeline (fetch → cache → tiers → overrides →
buffer → embed); `apps/city-view-3d/data/footprints.json` is the audit output.

## Related

- `docs/solutions/architecture-patterns/highlight-buildings-on-google-photorealistic-3d-tiles.md`
