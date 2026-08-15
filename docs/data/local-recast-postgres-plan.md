# Local Recast PostgreSQL Plan

Status: plan only. No data has been moved, exported, downloaded, or loaded.

## Question

Can we move the useful parts of the Outerspaces Supabase database onto the Acer/GB100 as a local PostgreSQL database named `recast`?

Yes, but the recommended path is not a blind full-database clone.

For Demo 1, create a local Recast edge mart:

```text
Outerspaces Supabase
  -> curated export manifest
  -> local PostgreSQL database named recast
  -> Recast demo/API/VSS workflows
```

Supabase remains the upstream source of truth until the team explicitly changes that architecture. The local `recast` database is the offline/demo/runtime copy optimized for the hackathon workflow.

## What The Current Docs Say

Current Demo 1 docs say:

- Supabase is the durable database and source of truth.
- The Acer GN100 should not be used as a second full database just to make the hardware visible.
- The Acer should hold the local inference working set needed for VSS, video evidence, embeddings, clips, and compact building context.
- The minimum Acer package was originally:

```text
1 building context JSON
1 known-good walkthrough/live source/clip
1 set of semantic VSS search prompts
1 local VSS embedding/index directory
1 output JSON file of observations and citations
```

That was the right recommendation when the goal was only to prove VSS. The new goal is broader: make the GB100 run a local Recast intelligence layer. That justifies local PostgreSQL, but still does not justify pulling every raw table by default.

## Recommendation

Create a local PostgreSQL database named:

```text
recast
```

Inside it, preserve source lineage with schemas:

```text
source_outerspaces  -- curated raw/subset tables from Outerspaces
recast              -- normalized Recast building intelligence tables/views
vss                 -- physical evidence, clips, observations, prompts, VSS results
capital             -- incentive/program reference and fit analysis
meta                -- export manifests, source checksums, sync timestamps
```

Do not call the local database `outerspaces`. Outerspaces is the upstream source. Recast is the product/runtime.

## Pull All Data Or Parts?

Start with parts.

Use tiers:

| Tier | Scope | Pull now? | Purpose |
| --- | --- | --- | --- |
| Tier 0 | Hero bundle: 2601 Elliott, Gibraltar backup, 1918 8th contrast, 1700 Westlake VSS test building | Yes | Smallest demo-safe package |
| Tier 1 | Downtown / Seattle office opportunity working set | Yes, after Tier 0 validates | Citywide opening, attention layer, hero ranking |
| Tier 2 | Lightweight citywide building spine | Maybe | Fast map/extrusion context without raw history bloat |
| Tier 3 | Full raw historical warehouse | No, unless proven necessary | Expensive, noisy, not needed for Demo 1 |

The default should be Tier 0 + Tier 1.

Only pull Tier 2 if the demo UI needs broader city context locally.

Do not pull Tier 3 unless the team has a specific query or offline requirement that cannot be served by curated tables.

## Current Known Outerspaces Tables

From current Demo 1 recon:

| Layer | Relation | Count | Local posture |
| --- | --- | ---: | --- |
| Building spine + coordinates | `warehouse.building_profile_with_coords` | 1,112,066 | Tier 1/2 candidate |
| Availability signal | `warehouse.availability_signal` | 63 | Tier 0/1 yes |
| Assessed value history | `warehouse.assessed_value_history` | 10,839,791 | Tier 0/1 filtered by selected buildings/area |
| Permit history | `warehouse.permit_history` | 2,977,869 | Tier 0/1 filtered by selected buildings/area |
| Private JLL availability raw | `private.jll_building_availability_raw` | 124 | Only if license/privacy permits; review-gated |
| Private JLL match | `private.jll_building_availability_match` | 124 | Only if license/privacy permits; review-gated |
| Private distress seed raw | `private.build_vitals_distress_seed_raw` | 25 | Optional, review-gated |
| Private distress seed match | `private.build_vitals_distress_seed_match` | 25 | Optional, review-gated |
| Full King County value history | `public.kingcounty_raw_value_history_full` | 27,557,132 | Do not pull by default |

## Tier 0 Demo Bundle

This is the first local `recast` database load.

Buildings:

- `2601 Elliott Ave` as #1 hero.
- `1518 3rd Ave / Gibraltar Tower` as #2 backup.
- `1918 8th Ave` as future-proofing contrast.
- `1700 Westlake` as VSS/mobile capture test building, if still used.

Data to include:

- building profile rows;
- parcel/building identifiers;
- coordinates and geometry needed for map/extrusion;
- latest assessed value fields;
- value history for those buildings/parcels;
- permit history for those buildings/parcels;
- availability signals;
- reviewed seed facts only if source-reviewed;
- incentive/program reference rows;
- Recast assessment scaffolding;
- VSS prompts and expected output fields.

Tier 0 should fit easily and be easy to validate manually.

## Tier 1 Opportunity Working Set

After Tier 0 works, create a broader working set for the citywide opening.

Candidate filters:

- Seattle or downtown/SLU geography;
- office, commercial, mixed-use, civic, retail, or other candidate classes;
- buildings above a demo-relevant gross-SF threshold;
- records with coordinates;
- value compression above a threshold;
- availability signal present;
- permit silence or notable permit activity;
- buildings referenced by public conversion/incentive context.

Tier 1 supports:

- attention layer;
- hero ranking;
- As-Is / trajectory panels;
- backup hero candidates;
- "Recast does not wait for buildings to die" contrast.

## Tier 2 Lightweight Citywide Spine

Optional.

Could include:

- building ID;
- address;
- coordinates;
- footprint/simplified geometry;
- asset class;
- gross SF;
- year built;
- stories;
- latest value;
- value compression summary;
- attention/recast flags.

Do not include full histories in Tier 2. Use summary columns/materialized views.

## Tier 3 Full Warehouse

Not recommended for Demo 1.

Reasons:

- It increases migration time and failure risk.
- It complicates indexing/storage.
- It may copy data the demo never uses.
- It increases privacy/licensing review burden.
- It makes the GB100 look like a database clone instead of a physical-AI engine.

Full raw history can remain in Supabase until the product has a concrete need for fully local analytics.

## Proposed Local Schema

### `source_outerspaces`

Curated upstream copies:

- `building_profile_with_coords_subset`
- `availability_signal`
- `assessed_value_history_subset`
- `permit_history_subset`
- `jll_building_availability_match_reviewed`
- `distress_seed_match_reviewed`

### `recast`

Product-ready intelligence:

- `building`
- `building_signal_snapshot`
- `building_value_trajectory`
- `building_permit_activity`
- `building_availability`
- `building_attention_candidate`
- `building_recast_assessment`
- `building_recast_option`

### `vss`

Physical evidence:

- `capture_session`
- `video_source`
- `vss_prompt`
- `vss_observation`
- `vss_clip`
- `evidence_claim`

### `capital`

Program/incentive intelligence:

- `program`
- `program_requirement`
- `program_fit`
- `capital_stack_option`

### `meta`

Lineage and validation:

- `source_export_manifest`
- `source_relation_snapshot`
- `load_run`
- `row_count_check`
- `validation_issue`

## Migration Mechanics

Plan only. Do not run until approved.

### 1. Inventory

Before moving data, run read-only metadata checks against Outerspaces:

- table sizes;
- row counts;
- primary/foreign keys or join keys;
- column lists;
- geometry column types;
- indexes;
- row counts after candidate filters.

Record results in a migration manifest before export.

### 2. Manifest

Create a checked-in manifest such as:

```text
docs/data/recast-local-db-manifest.md
```

The manifest should specify:

- source relation;
- destination schema/table;
- filter/query;
- expected row count;
- required columns;
- reason needed;
- privacy/licensing status;
- validation query.

### 3. Local PostgreSQL

Install/run PostgreSQL on the GB100 with a database named:

```sql
create database recast;
```

Use local-only credentials stored outside Git.

### 4. Export

For filtered subsets, prefer query-based exports rather than full table dumps:

```sql
COPY (
  SELECT ...
  FROM warehouse.building_profile_with_coords
  WHERE ...
) TO STDOUT WITH CSV HEADER;
```

Use full-table dump only for tiny relations such as `warehouse.availability_signal`.

### 5. Load

Load into `source_outerspaces` staging tables first.

Then build `recast`, `vss`, and `capital` product tables/views from those staged relations.

### 6. Validate

For every table:

- row count matches manifest;
- sample hero building rows exist;
- key joins work;
- value compression fields match source;
- availability fields match source;
- permit rows match source;
- no unreviewed private rows are promoted as facts;
- source timestamps and export timestamps are recorded.

### 7. Cutover For Demo

The demo app/API should read from local `recast` first.

If local database is unavailable, fallback options:

1. cached JSON hero bundle;
2. Supabase read-only API;
3. recorded demo outputs.

## What Should Not Move Yet

Do not move yet:

- full Supabase backups;
- all 27.5M full King County value-history rows;
- unrelated raw public tables;
- unreviewed private data as final truth;
- private credentials;
- raw private video;
- large media archives;
- personal/local machine paths;
- anything not used in the demo or immediate Recast app.

## Open Questions

Before implementation:

1. What is the GB100 disk layout and free space?
2. Will PostgreSQL run directly on host or in Docker?
3. Does the demo app need PostGIS locally, or are precomputed GeoJSON/vector tiles enough?
4. Which exact geography defines Tier 1: downtown only, Seattle citywide, King County, or selected corridors?
5. Are private JLL and distress seed rows allowed in the judge-facing demo if summarized and source-labeled?
6. Do we need local writes during the demo, or is read-mostly enough?
7. Should Supabase continue as upstream after the hackathon, or is this the beginning of a Recast-owned database migration?

## Recommended First Implementation Step

Do not move data yet.

First produce:

```text
docs/data/recast-local-db-manifest.md
```

Then run only metadata/count queries to estimate:

- filtered Tier 0 rows;
- filtered Tier 1 rows;
- expected disk size;
- needed indexes;
- load time.

After the manifest is approved, load Tier 0 into a local `recast` database and validate it end to end before expanding.
