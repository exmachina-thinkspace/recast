# Local Recast PostgreSQL Plan

Status: implemented through Tier 0 + Tier 1 public foundation on the GB100. No Mac mini persistent staging database was created.

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

## Implementation Status - 2026-08-15

The approved foundation is now running on the Acer GN100.

| Item | Actual |
| --- | --- |
| Machine | `gn100-3315`, Acer Veriton GN100 |
| OS | Ubuntu 24.04.4 LTS, Linux `6.17.0-1029-nvidia`, arm64 |
| PostgreSQL | PostgreSQL 16.14, native apt install |
| Cluster | `16/main`, port `5432`, data directory `/var/lib/postgresql/16/main` |
| Local database | `recast` |
| Local schemas | `source_outerspaces`, `recast`, `vss`, `capital`, `meta` |
| Disk | `/dev/nvme0n1p2`, 3.7T total, 3.1T available during setup |
| Docker | Installed, but not used for PostgreSQL |
| Secrets | GB100 local files under `~/.config/recast/`, outside Git |
| Transfer mechanism | GB100 direct `psql` pull from Outerspaces Supabase pooler |
| Mac mini role | Orchestration only; no persistent staging DB or export directory |
| Successful load run | `recast_20260816T003657Z` |
| Local DB footprint | `11 MB` after Tier 0 + Tier 1 public foundation plus review-gated JLL/distress rows |

The loader encountered intermittent GB100 DNS resolution failures against the Supabase pooler during early attempts. The checked-in loader now retries direct copy operations and records failed runs in `meta.load_run`.

## Webapp Access

The database name is `recast`. The app/team login role for read-only access is:

```text
recast_readonly
```

Use local PostgreSQL from services running on the GB100:

```text
host=127.0.0.1
port=5432
database=recast
user=recast_readonly
password=<ask Peter>
```

For application code, store the connection string outside Git:

```text
DATABASE_URL=postgresql://recast_readonly:<ask Peter>@127.0.0.1:5432/recast
```

Even when the webapp runs locally on the GB100, it should authenticate to PostgreSQL. Local means the database does not need to be exposed publicly; it does not mean unauthenticated access.

The browser/frontend should not connect directly to PostgreSQL. Server-side app code or an API route should connect to the local database and return only the data needed by the UI.

Current role posture:

| Role | Purpose | Posture |
| --- | --- | --- |
| `recast_app` | Loader/admin-style local Recast operations | Stored outside Git in GB100 local env files |
| `recast_readonly` | Webapp/team read access | `SELECT` on current `source_outerspaces`, `recast`, `vss`, `capital`, and `meta` tables; writes blocked |

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
- `jll_building_availability_match_gated`
- `distress_seed_match_gated`

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

Implemented for Tier 0 + Tier 1 public foundation in:

- `db/schema/001_local_recast.sql`
- `scripts/load-local-recast.sh`
- `scripts/validate-local-recast.sh`

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

This is complete on the GB100.

### 4. Export / Direct Pull

For filtered subsets, prefer query-based exports rather than full table dumps:

```sql
COPY (
  SELECT ...
  FROM warehouse.building_profile_with_coords
  WHERE ...
) TO STDOUT WITH CSV HEADER;
```

Use full-table dump only for tiny relations such as `warehouse.availability_signal`.

The current implementation does not dump files. It runs filtered `\copy (SELECT ...) TO STDOUT` queries from the GB100 against Outerspaces/Supabase and pipes them directly into local PostgreSQL.

### 5. Load

Load into `source_outerspaces` staging tables first.

Then build `recast`, `vss`, and `capital` product tables/views from those staged relations.

This is complete for public Tier 0 + Tier 1 source tables and the initial normalized `recast` layer. `vss` and `capital` schemas exist but are intentionally not populated with speculative data.

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

Validation is complete for source row counts. The latest successful run wrote 22 row-count checks to `meta.row_count_check`.

| Tier | Source relation | Expected | Actual | Status |
| --- | --- | ---: | ---: | --- |
| Tier 0 | `source_outerspaces.building_profile_with_coords_subset` | 4 | 4 | pass |
| Tier 1 | `source_outerspaces.building_profile_with_coords_subset` | 67 | 67 | pass |
| Tier 0 | `source_outerspaces.parcel_subset` | 4 | 4 | pass |
| Tier 1 | `source_outerspaces.parcel_subset` | 67 | 67 | pass |
| Tier 0 | `source_outerspaces.kingcounty_raw_parcel_subset` | 4 | 4 | pass |
| Tier 1 | `source_outerspaces.kingcounty_raw_parcel_subset` | 67 | 67 | pass |
| Tier 0 | `source_outerspaces.assessed_value_history_subset` | 198 | 198 | pass |
| Tier 1 | `source_outerspaces.assessed_value_history_subset` | 3,687 | 3,687 | pass |
| Tier 0 | `source_outerspaces.permit_history_subset` | 60 | 60 | pass |
| Tier 1 | `source_outerspaces.permit_history_subset` | 1,383 | 1,383 | pass |
| Tier 0 | `source_outerspaces.availability_signal` | 2 | 2 | pass |
| Tier 1 | `source_outerspaces.availability_signal` | 49 | 49 | pass |
| Tier 0 | `source_outerspaces.seattle_building_energy_benchmarking_subset` | 20 | 20 | pass |
| Tier 1 | `source_outerspaces.seattle_building_energy_benchmarking_subset` | 319 | 319 | pass |
| Tier 0 | `source_outerspaces.jll_building_availability_match_gated` | 1 | 1 | pass |
| Tier 1 | `source_outerspaces.jll_building_availability_match_gated` | 20 | 20 | pass |
| Tier 0 | `source_outerspaces.jll_building_availability_raw_gated` | 1 | 1 | pass |
| Tier 1 | `source_outerspaces.jll_building_availability_raw_gated` | 20 | 20 | pass |
| Tier 0 | `source_outerspaces.distress_seed_match_gated` | 1 | 1 | pass |
| Tier 1 | `source_outerspaces.distress_seed_match_gated` | 5 | 5 | pass |
| Tier 0 | `source_outerspaces.distress_seed_raw_gated` | 1 | 1 | pass |
| Tier 1 | `source_outerspaces.distress_seed_raw_gated` | 5 | 5 | pass |

Derived local Recast row counts after Tier overlap dedupe:

| Relation | Rows |
| --- | ---: |
| `recast.building` | 69 |
| `recast.building_signal_snapshot` | 69 |
| `recast.building_attention_candidate` | 69 |
| `recast.building_availability` | 69 |
| `recast.building_energy_signal` | 69 |
| `recast.building_permit_activity` | 69 |
| `recast.building_value_trajectory` | 67 |
| `recast.debt_maturity_signal` | 69 |

`recast.building_value_trajectory` has 67 rows because two of the 69 unique local buildings do not currently have usable assessed-value history in the loaded source subset.

`recast.debt_maturity_signal` currently keeps all buildings at `INSUFFICIENT_DEBT_EVIDENCE`. It records whether review-gated JLL/distress source rows exist, but it does not treat those rows as verified debt maturity or legal distress until recorder/court/licensed-source review is complete.

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

## Remaining Open Questions

Resolved:

1. GB100 disk layout and free space are sufficient for the approved edge mart.
2. PostgreSQL runs directly on the host, not in Docker.
3. PostGIS is not required for the current Tier 0 + Tier 1 manifest because the demo currently uses centroids, not local geometry operations.
4. Tier 1 is the documented 67-building downtown/SLU office opportunity working set.

Still open:

1. Are private JLL and distress seed rows allowed in the judge-facing demo if summarized and source-labeled?
2. Do we need local writes during the demo beyond generated Recast/VSS outputs?
3. Should Supabase continue as upstream after the hackathon, or is this the beginning of a Recast-owned database migration?
4. Should we add local PostGIS later after the building footprint join is verified?

## Recommended Next Implementation Step

Connect the Recast application/API to local PostgreSQL in read-only mode, using `recast.*` for product intelligence and `source_outerspaces.*` only when the UI needs to show underlying evidence.

Do not promote private/review-gated data into factual Recast conclusions or judge-facing claims until licensing, source review, and evidence posture are resolved.
