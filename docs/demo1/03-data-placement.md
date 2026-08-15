# Data Placement: Supabase vs. Acer GN100

## Recommendation

Keep the Acer GN100 lean. Put only the local inference working set on the machine.

Supabase remains the durable database. The Acer holds the data needed to make VSS semantic video search, physical-asset understanding, and evidence retrieval fast, private, and resilient during the demo.

Update: if the team chooses to run local PostgreSQL on the GB100, treat it as a curated Recast edge mart named `recast`, not a blind clone of the full Outerspaces/Supabase warehouse. See [../data/local-recast-postgres-plan.md](../data/local-recast-postgres-plan.md).

## Keep In Supabase

### Durable Records

- King County parcel records.
- Seattle building outlines.
- Address and PIN joins.
- Zoning and development capacity.
- Value and tax history.
- SDCI permit records.
- Public safety, complaint, service-request, and upkeep signals where used.
- Energy benchmarking records if acquired.
- Recast distress / early-warning outputs.
- Recast alternative-use assessments.
- verified incentive/program reference data and eligibility rules.
- capital-stack / incentive-fit reasoning outputs.
- Evidence claims and score history.

### App State

- hero building candidates;
- selected hero building;
- user sessions;
- saved Recast assessments;
- enrichment run status;
- audit logs;
- source metadata;
- evidence pointers.

### Why

These datasets are structured, relational, auditable, and better served by SQL/API access. They do not become more impressive merely because they are copied to edge hardware.

## Put On Acer GN100

### Required Demo Working Set

For each hero candidate or VSS test building, stage a compact context pack:

```text
building_id
address
parcel_id / PIN
building footprint geometry
selected permit metadata
selected plan image/page references
records signal snapshot
reuse constraints / zoning summary
capital-stack / incentive candidates
target questions for VSS
```

### Video Evidence

- live RTSP stream configuration, without committing full private URLs;
- walkthrough clips used in the demo;
- extracted keyframes;
- VSS clip IDs;
- frame thumbnails for UI references;
- local VSS embeddings and search indexes;
- local VSS logs needed for debugging.

### 3D / Spatial Assets

- citywide / district building massing assets;
- highlighted hero building geometry;
- optional simplified interior zone assets only if useful;
- coordinate transform metadata;
- camera placement / route metadata when available;
- evidence-to-zone anchors when reliable.

### Model / Runtime Cache

- VSS containers;
- model weights and NGC cache;
- Docker volumes;
- temporary vector/search stores used by VSS;
- generated summaries before write-back.

## Do Not Put On Acer GN100

- full citywide parcel warehouse;
- full Supabase backups;
- production credentials in files;
- raw private video beyond the demo retention window;
- unrelated datasets not used in the visible demo;
- large historical public datasets unless offline mode requires them.

## Minimum Acer Data Package

For demo1, the Acer should need only:

```text
1 building context JSON
1 known-good walkthrough/live source/clip
1 set of semantic VSS search prompts
1 local VSS embedding/index directory
1 output JSON file of observations and citations
```

That is enough to show the Acer as the physical-AI engine while Supabase stays the source of truth. Citywide 3D assets can live with the demo UI; they do not need to be copied to Acer unless local rendering requires it.

## Offline Mode Package

If venue internet is unreliable, prepare a separate offline package:

```text
demo1-offline/
  building-context.json
  footprint.geojson
  city-buildings.geojson
  clips/
  frames/
  expected-vss-output.json
  expected-recast-assessment.json
```

This package is a fallback, not the primary architecture.

## Write-Back Contract

The Acer returns only:

- observation text;
- evidence label;
- confidence;
- source type;
- clip ID;
- timestamp range;
- frame path or thumbnail path;
- spatial anchor if available;
- model/profile metadata;
- created_at timestamp.

No raw private video should be written back to Supabase.
