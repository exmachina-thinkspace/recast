# Demo 1 Recast Engineering Plan

Status: recon-gated engineering plan. Do not begin broad UI/application implementation until [00-recon-gate.md](00-recon-gate.md) is answered with evidence.

Purpose: define the end-to-end Recast demo path where Seattle-scale building intelligence explains what a building is today, where it is heading, what it could become, and what could make that transformation economically possible.

```text
How is this building doing, what could it become, and what could make that transformation work?
```

Canonical narrative spine:

```text
Seattle -> Building Intelligence Layer -> As-Is -> Trajectory -> Recast Opportunity -> Physical Understanding via NVIDIA VSS -> Alternative Futures -> Capital Stack / Incentive Intelligence -> Best Path Forward -> City Scale
```

The 2-3 minute demo should still use a distressed/zombie-style hero building if that is the most dramatic proof. The larger reveal is that Recast does not have to wait for a building to die; the same intelligence layer can future-proof healthier buildings while they still have choices.

## File Map

| File | Use |
| --- | --- |
| [00-recon-gate.md](00-recon-gate.md) | Evidence required before implementation starts |
| [01-demo-thesis.md](01-demo-thesis.md) | The concise story the engineering work must prove |
| [02-system-architecture.md](02-system-architecture.md) | Components, responsibilities, and data flow |
| [03-data-placement.md](03-data-placement.md) | What stays in Supabase vs. what belongs on the Acer GN100 |
| [04-agent-workflow.md](04-agent-workflow.md) | Agent workflow from database pull through evidence-backed output |
| [05-vss-gn100-plan.md](05-vss-gn100-plan.md) | VSS work needed on Acer GN100 |
| [06-3d-digital-twin.md](06-3d-digital-twin.md) | Citywide 3D visualization and optional interior twin |
| [07-recast-assessment.md](07-recast-assessment.md) | As-Is + Recast + capital-stack assessment |
| [08-build-sequence.md](08-build-sequence.md) | Engineering steps in execution order |
| [09-demo-script.md](09-demo-script.md) | Judge-facing demo sequence and proof moments |
| [10-acceptance-checklist.md](10-acceptance-checklist.md) | Concrete checks before calling demo1 ready |
| [11-current-recon-findings.md](11-current-recon-findings.md) | Current data/program recon and weekend-vs-roadmap call |

## Core Decision

Do not use the Acer GN100 as a second database.

Use Supabase for durable city/property records, joins, state, user-visible app data, and audit history. Use the Acer GN100 for the local working set that makes the demo special:

- VSS video ingestion and visual reasoning.
- Short local video clips, frames, embeddings, and clip citations.
- Searchable walkthrough memory: natural-language query -> relevant timestamp/clip.
- Small building context packs pulled from Supabase for the hero candidates.
- Temporary physical-evidence artifacts before durable write-back.

## Core Demo Path

```text
Seattle 3D flyover
  -> building intelligence layer
  -> choose one hero building
  -> explain As-Is and trajectory
  -> enter building evidence
  -> Acer GN100 / VSS searches physical asset
  -> Recast ranks plausible alternative futures
  -> capital-stack / incentive layer shows what could help economics
  -> best path forward with evidence, risks, and unknowns
  -> zoom back to city scale
```

## Building Roles

Do not assume the VSS test building is the Recast hero building.

| Role | Current candidate | Why |
| --- | --- | --- |
| VSS test building | `1700 Westlake Ave N` | Controlled access, permit/document context, existing Thinkspace Arlo -> Scrypted -> RTSP path |
| Recast hero building | TBD after recon | Must have the strongest As-Is weakness / trajectory / Recast opportunity story |

These can become the same building only if the distress evidence supports it.

## Explicit Non-Goals

- `ABCD` is not part of the required demo.
- Do not build a diligence-dispatch checklist as the payoff.
- Do not call one camera/clip a sensor-corrected whole-building score.
- Do not spend interior digital-twin time unless it improves the judge-visible Recast moment.
- Do not claim verified incentive eligibility unless program requirements actually support it.
