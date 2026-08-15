# Recast Agent Workflow

## Goal

Show the Recast loop: understand As-Is performance and trajectory, understand the physical asset with VSS, compare alternative futures, and identify what could make the best path economically possible.

## Workflow

### 1. Build As-Is / Trajectory View

Agent queries verified building-level signals from Supabase:

```text
value decline
availability / vacancy indicators
energy usage decline if available
permit silence or issue history
receivership / foreclosure / court signals if available
ownership / lender transition if available
location and zoning context
building age / obsolescence proxies
available incentives/program facts where relevant
```

Output:

```text
ranked candidate buildings + As-Is / trajectory explanation
```

### 2. Choose Hero Building

The hero building must be selected from evidence, not convenience. It may be distressed, vulnerable, or especially interesting because Recast opportunity is high relative to current As-Is performance.

`1700 Westlake Ave N` remains the VSS test building unless it also has a real distress story.

Agent resolves for the hero:

- canonical building ID;
- parcel/PIN;
- footprint geometry;
- distress signals;
- As-Is condition;
- trajectory signals;
- zoning/development context;
- incentive/program candidates;
- permit/document context;
- known gaps.

Output:

```text
hero-building-context.json
```

### 3. Explain As-Is And Trajectory

Agent creates a plain-English evidence explanation:

```text
Recast sees this building's current use and trajectory this way because signal A, signal B, and signal C are moving together.
```

Every signal must cite a source.

### 4. Prepare Acer / VSS Task

Agent creates a task package:

```text
building_id
address
records_summary
reuse_hypotheses
video_source_or_walkthrough
target_questions
optional_spatial_assets
write_back_endpoint
```

Target questions should be concrete:

- Show me empty or underutilized areas.
- Find large open floorplates.
- Show loading or service access.
- Find areas that look suitable for residential conversion.
- Find areas that look unsuitable for residential conversion.
- Show evidence of current use or low utilization.
- What cannot be concluded from this walkthrough?

### 5. Run VSS As Building Ctrl-F

On Acer GN100:

- confirm VSS is healthy;
- register live RTSP, known-good clip, or walkthrough;
- ingest enough footage for semantic search;
- run natural-language retrieval tasks;
- retrieve clips and timestamps supporting each observation.

### 6. Normalize Observations

Agent converts VSS output into claims:

```json
{
  "building_id": "demo-1700-westlake",
  "question": "Show me underutilized areas.",
  "claim": "The sampled walkthrough includes an open area with no visible occupants or active use during the clip.",
  "evidence_label": "OBSERVED",
  "confidence": 0.72,
  "source_type": "vss_clip",
  "source_ref": "vss://clip/...",
  "timestamp_start": "00:01:12",
  "timestamp_end": "00:01:28",
  "spatial_anchor": "unknown_or_zone_id",
  "limitations": "This supports underutilization in the observed clip only; it does not prove whole-building vacancy."
}
```

### 7. Build Recast Assessment

Agent combines:

- records signal;
- physical evidence;
- zoning/location constraints;
- candidate reuse scenarios;
- market/need evidence where present;
- potential capital-stack and incentive support;
- risks and unknowns.

Output:

```text
As-Is intelligence + ranked alternative futures + capital-stack support + evidence + risks + unknowns
```

### 8. Present To Judge

UI shows:

- Seattle distress flyover;
- hero building selected from evidence;
- distress explanation;
- VSS search query and retrieved clip;
- Recast recommendation;
- potential incentives / financing supports with verified vs. potential eligibility labels;
- zoom back to city scale.

## Evidence Rules

Every claim must be one of:

```text
KNOWN
OBSERVED
INFERRED
UNKNOWN
INSUFFICIENT_EVIDENCE
```

Never upgrade `INFERRED` to `OBSERVED` unless the claim points to a clip/frame/time range.

Never present an action as final professional feasibility. The output is triage.
