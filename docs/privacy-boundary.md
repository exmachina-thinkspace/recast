# Privacy Boundary

Owner: Namratha. Status: living document, updated 2026-08-15.

## What this document is for

Section 2 of `responsibility(1).md` gives Namratha authority over privacy-preserving pipeline design; Shauana and Craig hold joint go/no-go authority over what claims and displays are permitted. This document is the technical half of that boundary — what physically leaves the Spark box, and what never does.

## Stays on the Spark, never leaves

- **Raw RTSP video frames.** Detection runs in-process on the box (`services/vision-bridge/bridge.py`, `~/arlo-vision/live_detect.py`). No frame is written to any external service, uploaded, or included in an API response.
- **Depth/point-cloud data** (`depth_to_mesh.py` output). Used locally to build visualization meshes; not transmitted as part of any sensor observation event.
- **RTSP stream credentials/tokens.** Per `spark-3d-pipeline/SPARK-HANDOFF.md` section 6, these live only in Scrypted's own database on the Mac mini and are never committed to git or written to a config file in this repo. `configs/cameras.yaml` intentionally stores only camera IDs, zone names, and *env var names* — never the RTSP URLs themselves.
- **Face recognition or persistent identity.** Not implemented anywhere in the pipeline. Detection is class-level (`person`), not identity-level. There is no re-identification, no embedding storage tied to a person across frames or cameras.

## Leaves the Spark boundary (goes to the API / frontend)

Only the fields defined in `packages/contracts/sensor_observation.schema.json`:

```
event_id, building_id, space_id, event_type, value, unit,
evidence_tier, confidence, source, observed_at, expires_at
```

That's an aggregate count (e.g. "3 people in floor-02-lobby at 19:22"), never an image, never a bounding box, never a track ID. `additionalProperties: false` in the schema is a deliberate technical enforcement of this boundary — the sink (`services/api-sink/sink.py`) will reject any event carrying an unrecognized field.

## Approved narrative language

Per `responsibility(1).md` section 5.5 (Shauana's narrative contract), the grounded-explanation LLM is prompted to use:

- "potentially underused," not "vacant," unless independently established
- "observed low activity," not "empty building"
- "sensor-corrected," not "ground truth"
- "insufficient evidence," not a fabricated low score

This is enforced at the prompt level today (see `docs/model-evaluation.md`); it should also be enforced as a display-layer check once `apps/web` exists, so a future non-LLM code path can't reintroduce prohibited language.

## Open items

- No `docs/hero-building/access-and-consent.md` exists yet in-repo (Craig's deliverable) — this document assumes consented display of aggregate occupancy for the hero building but does not itself establish that consent.
- Camera-zone calibration is not done (see `docs/ai-architecture.md`), so `space_id` assignment is presently a static mapping in `configs/cameras.yaml`, not a verified geometric one. This is a data-quality gap, not a privacy gap — no additional data leaves the boundary as a result.
