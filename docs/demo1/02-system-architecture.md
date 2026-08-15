# Recast System Architecture

## Components

```text
Supabase
  |
  | city/building signals + incentive/program facts + hero candidate context
  v
Agent Orchestrator
  |
  | distress explanation + VSS/search tasks
  v
Acer GN100
  |
  | local VSS + walkthrough embeddings + physical evidence clips
  v
Recast Assessment Worker
  |
  | As-Is state + Recast alternatives + capital-stack fit + unknowns
  v
Supabase
  |
  | API
  v
Demo UI
```

## Responsibilities

### Supabase

Supabase is the source of truth for:

- building identity;
- parcel, PIN, address, owner/entity links when available;
- building footprint geometry;
- zoning/development capacity;
- assessed value and tax history;
- permit records and document metadata;
- distress, vacancy, safety, upkeep, availability, receivership, debt, and energy signals only where actually present and verified;
- incentive/program reference facts and eligibility rules as they are verified;
- durable enriched observations;
- As-Is assessments, Recast assessments, and capital-stack/incentive reasoning outputs;
- run logs and audit trail.

### Agent Orchestrator

The agent:

- ranks candidate distressed buildings from verified signals;
- selects or accepts a hero building;
- explains As-Is condition and trajectory;
- pulls the minimum context packet from Supabase;
- sends walkthrough/video search tasks to the Acer GN100;
- asks VSS semantic physical-asset questions;
- normalizes observations into evidence-labeled claims;
- combines records signal, physical evidence, and incentive/program facts into a Recast assessment;
- ranks plausible next uses with pros, economics support, risks, and unknowns.

### Acer GN100

The Acer GN100 is the local physical-AI workstation:

- VSS video ingestion;
- semantic video search across walkthroughs;
- visual Q&A, summarization, and clip/frame retrieval;
- local video embeddings and search index;
- temporary multimodal working memory;
- local evidence extraction before durable write-back.

### Demo UI

The UI shows:

- cinematic Seattle / downtown 3D flyover;
- early-warning distress layer with many gray insufficient-evidence buildings;
- hero building profile;
- source-backed distress explanation;
- VSS building-search queries and retrieved clips;
- evidence clips and frames;
- records signal + physical evidence + Recast assessment;
- ranked alternative-use recommendation;
- potential capital-stack / incentive support with verified vs. potential eligibility clearly separated.

## Data Flow

1. UI opens on Seattle / downtown from above.
2. Supabase/API returns verified As-Is / trajectory signals for a bounded building set.
3. Recast highlights candidate buildings and explains the building intelligence layer.
4. User selects the hero building.
5. Agent pulls the hero building context packet from Supabase.
6. Agent sends walkthrough/video tasks to the Acer.
7. Acer VSS ingests or searches the known-good clip/walkthrough.
8. VSS returns matching timestamps/clips for natural-language asset questions.
9. Agent converts raw observations into evidence claims:

   ```text
   KNOWN / OBSERVED / INFERRED / UNKNOWN / INSUFFICIENT_EVIDENCE
   ```

10. Agent combines records signal, physical evidence, regulatory/zoning context, and incentive/program facts.
11. UI renders As-Is, alternative futures, capital-stack support, factors, risks, and unknowns.
12. UI zooms back to city scale.

## Failure Boundaries

If hero-building video is unavailable:

- use `1700 Westlake Ave N` only as the VSS capability proof;
- do not pretend it is the distressed hero unless verified.

If live video fails:

- use a pre-captured clip from the same camera route;
- keep the Acer/VSS flow the same;
- mark the source as recorded, not live.

If interior 3D anchoring is not reliable:

- keep citywide 3D as the story layer;
- use clip retrieval instead of an interior twin;
- do not claim room-level precision.

If VSS cannot answer a task:

- store `INSUFFICIENT_EVIDENCE`;
- show the missing information checklist.
