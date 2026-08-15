# 3D Visualization Plan

## Objective

Separate the citywide 3D storytelling layer from the optional interior digital twin.

## Priority 1: Citywide 3D Recast View

The citywide visualization is central to the demo.

It should show:

- Seattle / downtown from above;
- neutral gray buildings where evidence is insufficient;
- a small set of buildings highlighted by As-Is vulnerability, trajectory change, or Recast opportunity;
- one hero building selected from the candidate set;
- zoom-in and zoom-back-out transitions.

This is the visual scale moment. Do this before spending time on an interior twin.

## Priority 2: Optional Interior Twin

Interior digital-twin work is optional. It should be built only if it improves the VSS/Recast wow moment.

Useful interior twin behavior:

- "This clip came from this floor/zone."
- "These zones were observed; these remain unknown."
- "This space supports or weakens the reuse hypothesis."

Not worth building for demo1:

- room-level precision from weak evidence;
- complex GLB/BIM conversion;
- a polished interior model that does not improve the recommendation.

## Data Inputs For Citywide View

- building footprint polygons;
- approximate height or floor count if available;
- As-Is / trajectory / Recast-opportunity state;
- insufficient-evidence state;
- selected hero building;
- camera path or viewport choreography.

## Citywide Data Contract

```json
{
  "buildings": [
    {
      "building_id": "string",
      "address": "string",
      "geometry_ref": "string",
      "height_m": 0,
      "recast_signal": 0.0,
      "evidence_state": "KNOWN|INFERRED|INSUFFICIENT_EVIDENCE",
      "is_hero": false
    }
  ]
}
```

## Visual Requirements

The citywide view should show:

- gray insufficient-evidence buildings;
- highlighted candidate buildings;
- hero building transition;
- explanation panel for "why this building?";
- return-to-city scale.

## Engineering Steps

1. Generate/extract downtown building massing from available footprints.
2. Attach verified early-warning scores or staged candidate scores.
3. Render gray/neutral default state.
4. Highlight 3-5 candidate buildings.
5. Create a camera path into the hero building.
6. Link hero building to evidence explanation and VSS results.
7. Add optional interior zone markers only if reliable.

## Guardrails

- Do not call it a complete BIM unless a real BIM exists.
- Do not imply precise measurements from uncalibrated video.
- Do not claim full-building condition from one camera.
- Do not hide unobserved space.

## Demo Line

```text
Gray does not mean healthy. It means Recast does not yet have enough evidence. The highlighted buildings are where multiple signals are changing.
```
