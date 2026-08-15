# Local Recast Data Manifest

Status: implemented for approved Tier 0 + Tier 1 public foundation on the GB100. Private/review-gated data remains unloaded.

Generated: 2026-08-15.

Source system inspected: Outerspaces Supabase, project `ortlrwcparuyvwzarczg`, read-only metadata/count queries through the pooler.

Target runtime concept:

```text
GB100 local PostgreSQL database: recast
```

## Executive Summary

The local GB100 database should start as a curated Recast edge mart, not a full Outerspaces clone.

Recommended movement scope after approval:

```text
Tier 0 + Tier 1 only
```

Tier 0 is the four-building demo bundle:

- `2601 Elliott Ave` - hero;
- `1518 3rd Ave / Gibraltar Tower` - backup;
- `1918 8th Ave` - future-proofing contrast;
- `1700 Westlake Ave N` - VSS/mobile capture test building.

Tier 1 is a reproducible Seattle downtown/SLU office opportunity working set:

```text
Seattle buildings inside bbox:
  latitude 47.58 to 47.66
  longitude -122.37 to -122.30

office-like:
  asset_class = Office
  OR source_use_description ILIKE '%office%'

minimum size:
  gross_sf >= 25,000

attention condition:
  peak_to_current_compression_pct <= -25
  OR availability_signal exists
  OR permit_count = 0
  OR latest_permit_date < 2019-01-01
```

Read-only count result:

```text
downtown/SLU all buildings:        5,513
downtown/SLU office-like:            941
office-like >= 25k SF:               431
recommended Tier 1 attention set:     67
```

This is the smallest useful Tier 1 I found: broad enough for a citywide Recast attention layer, but small enough to remain inspectable, defensible, and demo-safe.

## Implementation Result - 2026-08-15

The approved Tier 0 + Tier 1 public foundation was loaded directly from Outerspaces/Supabase into the GB100 local PostgreSQL database named `recast`.

| Item | Result |
| --- | --- |
| Load run | `recast_20260815T223718Z` |
| Transfer | GB100 direct `psql` pull from Supabase pooler |
| Mac staging | None |
| Local database | `recast` |
| Source schema loaded | `source_outerspaces` |
| Derived schema built | `recast` |
| Empty future schemas created | `vss`, `capital` |
| Lineage schema | `meta` |
| Validation | 14/14 source row-count checks passed |
| Recorded validation | `meta.row_count_check` contains 14 checks for the successful run |
| Database footprint | `10 MB` |

The source tables intentionally preserve Tier 0 and Tier 1 rows separately, even when a hero building also appears in the Tier 1 attention set. Recast-derived tables dedupe those overlaps into 69 unique local buildings.

## Loaded Source Tables

| Destination | Tier 0 rows | Tier 1 rows | Status |
| --- | ---: | ---: | --- |
| `source_outerspaces.building_profile_with_coords_subset` | 4 | 67 | loaded + validated |
| `source_outerspaces.parcel_subset` | 4 | 67 | loaded + validated |
| `source_outerspaces.kingcounty_raw_parcel_subset` | 4 | 67 | loaded + validated |
| `source_outerspaces.assessed_value_history_subset` | 198 | 3,687 | loaded + validated |
| `source_outerspaces.permit_history_subset` | 60 | 1,383 | loaded + validated |
| `source_outerspaces.availability_signal` | 2 | 49 | loaded + validated |
| `source_outerspaces.seattle_building_energy_benchmarking_subset` | 20 | 319 | loaded + validated |

Support tables loaded:

| Destination | Rows | Status |
| --- | ---: | --- |
| `source_outerspaces.market` | 3 | loaded |
| `source_outerspaces.asset_class_taxonomy` | 255 | loaded |

## Built Recast Tables

| Relation | Rows | Notes |
| --- | ---: | --- |
| `recast.building` | 69 | Unique local building spine after Tier overlap dedupe |
| `recast.building_signal_snapshot` | 69 | Initial source-backed signal snapshot |
| `recast.building_attention_candidate` | 69 | Initial opportunity/attention layer from source filters |
| `recast.building_availability` | 69 | Availability where source rows exist, nullable otherwise |
| `recast.building_energy_signal` | 69 | Energy rows summarized where source rows exist |
| `recast.building_permit_activity` | 69 | Permit activity summary |
| `recast.building_value_trajectory` | 67 | Two local buildings lack usable assessed-value history in the loaded subset |

No `capital.*` or `vss.*` rows were generated because those layers require real program-fit analysis and real VSS/video evidence, not speculative filler.

## Tier Definitions

| Tier | Scope | Include now? | Purpose |
| --- | --- | --- | --- |
| Tier 0 | Four named demo/test buildings | Yes | Hero, backup, contrast, VSS test |
| Tier 1 | 67 downtown/SLU office opportunity buildings | Yes | Recast attention layer beyond hand-picked buildings |
| Tier 2 | Lightweight Seattle/citywide building spine | No | Optional later map context |
| Tier 3 | Full Outerspaces/raw warehouse | No | Not needed for Demo 1 |

## Boundary Rule

### `source_outerspaces`

Use this schema only for source-shaped data brought from Outerspaces. Tables should remain recognizable as curated subsets of upstream relations.

Do not put Recast scores, decisions, recommendations, rewritten meanings, or generated intelligence here.

### `recast`

Use this schema for Recast-created intelligence:

- normalized building records;
- signal snapshots;
- As-Is labels;
- trajectory labels;
- opportunity candidates;
- recommendations;
- evidence summaries.

This distinction lets the demo say:

```text
This is what the underlying data said.
This is what Recast inferred from it.
```

## Demo 1 Requirement To Data Source Mapping

| Demo 1 requirement | Status | Source / derivation |
| --- | --- | --- |
| Four hero/test properties | AVAILABLE | `warehouse.building_profile_with_coords` by exact `building_id` / `source_parcel_id` |
| Property/building context | AVAILABLE | `warehouse.building_profile_with_coords`, `warehouse.parcel`, `public.kingcounty_raw_parcel` |
| Coordinates | AVAILABLE | `warehouse.building_profile_with_coords.parcel_centroid_lat/lon`, `warehouse.parcel.latitude/longitude` |
| Building footprint/massing | PARTIAL | `raw.building_shapefile_features.geometry_wkt` exists, but exact join key to Recast building IDs still needs a focused check |
| Asset class/use/size/stories/year built | AVAILABLE | `warehouse.building_profile_with_coords` |
| Assessed value trajectory | AVAILABLE | `warehouse.assessed_value_history`; summary fields also in `warehouse.building_profile_with_coords` |
| Value compression | AVAILABLE | `warehouse.building_profile_with_coords.peak_to_current_compression_pct/value` |
| Permit/investment activity | AVAILABLE | `warehouse.permit_history`; summary fields also in `warehouse.building_profile_with_coords` |
| Availability/vacancy signal | AVAILABLE, LIMITED | `warehouse.availability_signal`; private JLL rows are review/licensing-gated |
| Ownership proxy | AVAILABLE, LIMITED | `warehouse.building_profile_with_coords.owner_proxy`; `public.owner_alias` and `warehouse.owner_portfolio_insight` are review-sensitive |
| Sale/purchase context | AVAILABLE, LIMITED | latest sale fields in `warehouse.building_profile_with_coords`; `warehouse.sale_history` exists but count query timed out in this pass |
| Energy performance | AVAILABLE, UNJOINED | `raw.seattle_building_energy_benchmarking`; parcel/address match works for Tier 0/Tier 1 but should remain source-shaped until normalized |
| Regulatory/zoning context | AVAILABLE | `warehouse.parcel.zoning`, `public.kingcounty_raw_parcel.current_zoning`, present-use fields |
| Capital-stack / incentive programs | DERIVED / MANUAL-SEED | Use Recast-created `capital.program` records from authoritative sources; not currently a verified Outerspaces product table |
| VSS/video evidence | GENERATED LOCALLY | `vss.*` tables generated by mobile/VSS workflow, not copied from Outerspaces |
| Recast recommendation | DERIVED | `recast.building_recast_assessment`, `recast.building_recast_option`, `capital.program_fit` |
| Court/receivership/debt distress | MISSING / REVIEW-GATED | Some unverified seed claims exist; no durable verified legal/debt table confirmed |
| Structural/code/MEP conversion feasibility | MISSING | Requires external diligence or documents not confirmed in current schema |
| Exact conversion economics | MISSING | No cost model or underwriting table confirmed |

## Proposed Source Tables

### 1. `warehouse.building_profile_with_coords`

What it contains:

Building profile view with market, address, asset class, use, size, stories, year built, owner proxy, sale summary, current/peak value summary, permit summary, supported/missing signal flags, and parcel centroid.

Verified count:

```text
Full relation: 1,112,066 rows
Tier 0: 4 rows
Tier 1: 67 rows
```

Key fields:

- `market_id`
- `market_name`
- `city`
- `state`
- `source_jurisdiction`
- `source_parcel_id`
- `building_id`
- `address`
- `asset_class`
- `source_use_code`
- `source_use_description`
- `gross_sf`
- `net_sf`
- `stories`
- `year_built`
- `building_age`
- `effective_year`
- `owner_proxy`
- `latest_sale_date`
- `latest_sale_price`
- `sale_instrument`
- `sale_reason`
- `property_class`
- `current_assessment_year`
- `current_assessed_value`
- `peak_assessment_year`
- `peak_assessed_value`
- `peak_to_current_compression_pct`
- `peak_to_current_compression_value`
- `purchase_basis_compression_pct`
- `permit_count`
- `latest_permit_date`
- `permit_value_since_2019`
- `supported_signals`
- `missing_signal_flags`
- `artifact_risk_flags`
- `signal_confidence`
- `parcel_centroid_lat`
- `parcel_centroid_lon`

Destination:

```text
source_outerspaces.building_profile_with_coords_subset
```

Derived destination:

```text
recast.building
recast.building_signal_snapshot
recast.building_attention_candidate
```

Refresh strategy:

Static snapshot for Demo 1. Periodic sync later.

Privacy/licensing/provenance:

Public/derived property data, but preserve upstream source fields and do not present derived flags as verified distress without evidence labels.

### 2. `warehouse.assessed_value_history`

What it contains:

Parcel-level assessed value history by year.

Verified count:

```text
Full relation: 10,839,791 rows
Tier 0: 198 rows
Tier 1: 3,687 rows
```

Key fields:

- `market_id`
- `source_parcel_id`
- `assessment_year`
- `land_value`
- `improvement_value`
- `total_assessed_value`
- `value_reason`
- `source_table`
- `source_updated_at`

Destination:

```text
source_outerspaces.assessed_value_history_subset
```

Derived destination:

```text
recast.building_value_trajectory
recast.building_signal_snapshot
```

Refresh strategy:

Static snapshot for Demo 1. Periodic sync later.

Privacy/licensing/provenance:

Public assessor-derived data. Keep source year/value fields intact.

### 3. `warehouse.permit_history`

What it contains:

Parcel-level permit history, including permit number, type, status, issue date, value, percent complete, and description.

Verified count:

```text
Full relation: 2,977,869 rows
Tier 0: 60 rows
Tier 1: 1,383 rows
```

Key fields:

- `market_id`
- `source_parcel_id`
- `permit_number`
- `permit_type`
- `permit_status`
- `issue_date`
- `permit_value`
- `percent_complete`
- `description`
- `source_table`
- `source_updated_at`

Destination:

```text
source_outerspaces.permit_history_subset
```

Derived destination:

```text
recast.building_permit_activity
recast.building_signal_snapshot
```

Refresh strategy:

Static snapshot for Demo 1. Periodic sync later.

Privacy/licensing/provenance:

Public permit-derived data. Descriptions may include operational detail; keep as source evidence, not as final Recast conclusions.

### 4. `warehouse.availability_signal`

What it contains:

Small building-level availability/vacancy signal table with availability square footage, availability percentage, source, source URL, last observed date, confidence, and value-compression summary.

Verified count:

```text
Full relation: 63 rows
Tier 0: 2 rows
Tier 1: 49 rows
```

Key fields:

- `availability_signal_id`
- `building_id`
- `market_id`
- `source_parcel_id`
- `address`
- `asset_class`
- `gross_sf`
- `owner_proxy`
- `availability_sf`
- `direct_available_sf`
- `sublease_available_sf`
- `availability_pct`
- `source`
- `source_type`
- `source_url`
- `collection_method`
- `last_observed`
- `confidence`
- `coverage_status`
- `match_confidence`
- `notes`
- `current_assessed_value`
- `peak_assessed_value`
- `peak_to_current_compression_pct`

Destination:

```text
source_outerspaces.availability_signal
```

Derived destination:

```text
recast.building_availability
recast.building_signal_snapshot
```

Refresh strategy:

Static snapshot for Demo 1. Periodic sync later if source updates.

Privacy/licensing/provenance:

This is a derived availability layer. Keep source, confidence, coverage status, and last-observed fields visible.

### 5. `warehouse.parcel`

What it contains:

Parcel identity, normalized parcel ID, address, coordinates, lot square footage, zoning, present-use raw, and source metadata.

Verified count:

```text
Tier 0: 4 rows
Tier 1: 67 rows
```

Key fields:

- `market_id`
- `source_jurisdiction`
- `source_parcel_id`
- `normalized_parcel_id`
- `major`
- `minor`
- `address`
- `city`
- `state`
- `zip`
- `latitude`
- `longitude`
- `lot_sqft`
- `zoning`
- `present_use_raw`
- `source_table`
- `source_updated_at`

Destination:

```text
source_outerspaces.parcel_subset
```

Derived destination:

```text
recast.building
recast.building_signal_snapshot
```

Refresh strategy:

Static snapshot for Demo 1.

Privacy/licensing/provenance:

Public parcel-derived data.

### 6. `public.kingcounty_raw_parcel`

What it contains:

Raw King County parcel attributes with zoning, highest-and-best-use fields, present use, lot/site constraints, hazard/environmental flags, view/waterfront fields, and imported-at metadata.

Verified count:

```text
Full relation: present in public schema
Tier 0: 4 rows
Tier 1: 67 rows
```

Key fields for Recast:

- `parcel_id`
- `major`
- `minor`
- `prop_name`
- `prop_type`
- `current_zoning`
- `hbu_as_if_vacant`
- `hbu_as_improved`
- `present_use`
- `sq_ft_lot`
- `water_system`
- `sewer_system`
- `access`
- `topography`
- `street_surface`
- `inadequate_parking`
- `unbuildable`
- `traffic_noise`
- `contamination`
- `historic_site`
- `current_use_designation`
- hazard/sensitive-area fields where populated
- `latitude`
- `longitude`
- `imported_at`

Destination:

```text
source_outerspaces.kingcounty_raw_parcel_subset
```

Derived destination:

```text
recast.building_signal_snapshot
recast.building_recast_assessment
```

Refresh strategy:

Static snapshot for Demo 1.

Privacy/licensing/provenance:

Public assessor data. Treat site constraints as source claims; do not infer code feasibility without diligence.

### 7. `raw.seattle_building_energy_benchmarking`

What it contains:

Seattle building energy benchmarking rows with OSE building ID, data year, building name/type, tax parcel ID, address, GFA, Energy Star score, site/source EUI, energy use, emissions, and compliance status.

Verified count:

```text
Full relation: 34,699 rows
Tier 0 parcel matches: 20 rows
Tier 1 parcel matches: 319 rows
```

Key fields:

- `ose_building_id`
- `data_year`
- `building_name`
- `building_type`
- `tax_parcel_identification_number`
- `address`
- `latitude`
- `longitude`
- `year_built`
- `number_of_floors`
- `property_gfa_total`
- `energy_star_score`
- `site_eui_wn_kbtu_sf`
- `site_eui_kbtu_sf`
- `site_energy_use_kbtu`
- `source_eui_kbtu_sf`
- `electricity_kwh`
- `natural_gas_therms`
- `compliance_status`
- `compliance_issue`
- `total_ghg_emissions`
- `ghg_emissions_intensity`
- `jurisdiction`
- `loaded_at`

Destination:

```text
source_outerspaces.seattle_building_energy_benchmarking_subset
```

Derived destination:

```text
recast.building_energy_signal
recast.building_signal_snapshot
```

Refresh strategy:

Static snapshot for Demo 1. Periodic sync later.

Privacy/licensing/provenance:

Public city benchmarking data. It is source data, but the join/normalization is Recast-derived and should be recorded separately.

### 8. `private.jll_building_availability_raw`

What it contains:

Private extracted JLL availability rows with property address, rentable building area, percent leased, available square footage, asking rent posture, owner name, extraction text/confidence, and raw payload.

Verified count:

```text
Full relation: 124 rows
Tier 0 direct raw address match: 1 row
```

Key fields:

- `jll_raw_id`
- `source_report`
- `source_file_name`
- `source_page`
- `source_row_number`
- `extracted_at`
- `building_name`
- `property_address`
- `city`
- `state`
- `submarket`
- `property_type`
- `rentable_building_area_sf`
- `percent_leased`
- `available_min_sf`
- `available_total_sf`
- `max_contiguous_sf`
- `available_share_of_rba`
- `asking_rent`
- `rent_posture`
- `occupancy_timing`
- `owner_name`
- `extraction_confidence`
- `review_status`
- `reviewer_notes`

Destination:

```text
source_outerspaces.jll_building_availability_raw_reviewed
```

Derived destination:

```text
recast.building_availability
recast.building_signal_snapshot
```

Refresh strategy:

Static snapshot only if approved.

Privacy/licensing/provenance:

Private/extracted market data. Do not copy or show raw extraction text/payload in judge-facing materials unless rights are confirmed. Only promote reviewed/summarized facts.

### 9. `private.jll_building_availability_match`

What it contains:

Private match table connecting JLL raw rows to Outerspaces building IDs and parcels, with match status/confidence/review fields.

Verified count:

```text
Full relation: 124 rows
Tier 0: 1 row
Tier 1: 20 rows
```

Key fields:

- `jll_match_id`
- `jll_raw_id`
- `source_parcel_id`
- `building_id`
- `matched_address`
- `matched_building_name`
- `match_status`
- `match_method`
- `match_confidence`
- `match_score`
- `candidate_rank`
- `candidate_count`
- `review_status`
- `reviewer_notes`

Destination:

```text
source_outerspaces.jll_building_availability_match_reviewed
```

Derived destination:

```text
recast.building_availability
```

Refresh strategy:

Static snapshot only if approved.

Privacy/licensing/provenance:

Review-gated. Do not treat unreviewed matches as truth.

### 10. `private.build_vitals_distress_seed_raw`

What it contains:

Human-curated seed distress rows, including claimed distress types, claim summaries, claimed vacancy/value/debt values, evidence/review status, and source URLs.

Verified count:

```text
Full relation: 25 rows
Tier 0 direct raw address match: 1 row
```

Key fields:

- `seed_id`
- `source_key`
- `tier`
- `building_name`
- `address`
- `city`
- `state`
- `distress_types`
- `claim_summary`
- `vacancy_pct_claimed`
- `availability_sf_claimed`
- `value_decline_pct_claimed`
- `debt_amount_claimed`
- `evidence_status`
- `review_status`
- `source_note`
- `source_urls`

Destination:

```text
source_outerspaces.distress_seed_raw_reviewed
```

Derived destination:

```text
recast.building_signal_snapshot
```

Refresh strategy:

Static snapshot only after review.

Privacy/licensing/provenance:

All known rows are seed/review-gated. Do not use as verified distress unless source review is complete.

### 11. `private.build_vitals_distress_seed_match`

What it contains:

Match table connecting distress seed rows to building IDs/parcels.

Verified count:

```text
Full relation: 25 rows
Tier 0: 1 row
Tier 1: 5 rows
```

Key fields:

- `seed_id`
- `source_key`
- `match_status`
- `match_method`
- `match_confidence`
- `market_id`
- `building_id`
- `source_parcel_id`
- `matched_address`
- `matched_building_name`
- `review_notes`
- `reviewed_by`
- `reviewed_at`

Destination:

```text
source_outerspaces.distress_seed_match_reviewed
```

Derived destination:

```text
recast.building_signal_snapshot
```

Refresh strategy:

Static snapshot only after review.

Privacy/licensing/provenance:

Review-gated. Preserve `UNVERIFIED_SEED` / review labels.

### 12. `warehouse.asset_class_taxonomy`

What it contains:

Small taxonomy mapping source use codes/descriptions to asset classes and market/non-market flags.

Verified count:

```text
Full relation: 255 rows
```

Destination:

```text
source_outerspaces.asset_class_taxonomy
```

Derived destination:

```text
recast.building
```

Refresh strategy:

Static snapshot.

Privacy/licensing/provenance:

Internal normalization metadata. Safe to copy if treated as source mapping.

### 13. `warehouse.market`

What it contains:

Small market registry.

Verified count:

```text
Full relation: 3 rows
```

Destination:

```text
source_outerspaces.market
```

Refresh strategy:

Static snapshot.

### 14. `raw.building_shapefile_features`

What it contains:

Raw building footprint features with `geometry_wkt`, source EPSG, raw attributes, row hash, and ingest timestamp.

Verified structure:

- `source_feature_id`
- `raw_attributes`
- `source_epsg`
- `geometry_wkt`
- `row_hash`
- `ingested_at`

Destination:

```text
source_outerspaces.building_footprint_features_subset
```

Derived destination:

```text
recast.building_geometry
```

Refresh strategy:

Static snapshot once join/filter is verified.

Privacy/licensing/provenance:

Public GIS-derived. Exact join key to Recast building IDs was not confirmed in this pass; do not rely on this table until a focused geometry join probe is completed.

## Proposed Destination Tables

### `source_outerspaces`

| Destination table | Source | Tier |
| --- | --- | --- |
| `market` | `warehouse.market` | Tier 0/1 support |
| `asset_class_taxonomy` | `warehouse.asset_class_taxonomy` | Tier 0/1 support |
| `building_profile_with_coords_subset` | `warehouse.building_profile_with_coords` | Tier 0/1 |
| `parcel_subset` | `warehouse.parcel` | Tier 0/1 |
| `kingcounty_raw_parcel_subset` | `public.kingcounty_raw_parcel` | Tier 0/1 |
| `assessed_value_history_subset` | `warehouse.assessed_value_history` | Tier 0/1 |
| `permit_history_subset` | `warehouse.permit_history` | Tier 0/1 |
| `availability_signal` | `warehouse.availability_signal` | Tier 0/1 |
| `seattle_building_energy_benchmarking_subset` | `raw.seattle_building_energy_benchmarking` | Tier 0/1 |
| `jll_building_availability_raw_reviewed` | `private.jll_building_availability_raw` | optional/review-gated |
| `jll_building_availability_match_reviewed` | `private.jll_building_availability_match` | optional/review-gated |
| `distress_seed_raw_reviewed` | `private.build_vitals_distress_seed_raw` | optional/review-gated |
| `distress_seed_match_reviewed` | `private.build_vitals_distress_seed_match` | optional/review-gated |
| `building_footprint_features_subset` | `raw.building_shapefile_features` | optional until join verified |

### `recast`

| Destination table | Type | Source |
| --- | --- | --- |
| `building` | derived/normalized | source profiles, parcels, taxonomy |
| `building_geometry` | derived/normalized | footprint/centroid inputs |
| `building_signal_snapshot` | derived | current source signals per building |
| `building_value_trajectory` | derived | assessed value history |
| `building_permit_activity` | derived | permit history |
| `building_availability` | derived | availability and reviewed private availability |
| `building_energy_signal` | derived | energy benchmarking subset |
| `building_attention_candidate` | derived | Tier 1 attention filter and evidence |
| `building_recast_assessment` | generated locally | Recast assessment worker |
| `building_recast_option` | generated locally | Recast future-use comparison |

### `vss`

Generated locally, not copied from Outerspaces:

- `capture_session`
- `video_source`
- `vss_prompt`
- `vss_observation`
- `vss_clip`
- `evidence_claim`

### `capital`

Mostly generated/curated locally:

- `program`
- `program_requirement`
- `program_fit`
- `capital_stack_option`

Initial `capital.program` rows should come from authoritative public program sources already identified in Demo 1 docs, not from Outerspaces warehouse tables.

### `meta`

Generated locally:

- `source_export_manifest`
- `source_relation_snapshot`
- `load_run`
- `row_count_check`
- `validation_issue`

## Source To Destination Mapping

| Source | Filter | Destination | Expected rows |
| --- | --- | --- | ---: |
| `warehouse.market` | all rows | `source_outerspaces.market` | 3 |
| `warehouse.asset_class_taxonomy` | all rows | `source_outerspaces.asset_class_taxonomy` | 255 |
| `warehouse.building_profile_with_coords` | Tier 0 exact IDs | `source_outerspaces.building_profile_with_coords_subset` | 4 |
| `warehouse.building_profile_with_coords` | Tier 1 filter | `source_outerspaces.building_profile_with_coords_subset` | 67 |
| `warehouse.parcel` | Tier 0 parcels | `source_outerspaces.parcel_subset` | 4 |
| `warehouse.parcel` | Tier 1 parcels | `source_outerspaces.parcel_subset` | 67 |
| `public.kingcounty_raw_parcel` | Tier 0 parcels | `source_outerspaces.kingcounty_raw_parcel_subset` | 4 |
| `public.kingcounty_raw_parcel` | Tier 1 parcels | `source_outerspaces.kingcounty_raw_parcel_subset` | 67 |
| `warehouse.assessed_value_history` | Tier 0 parcels | `source_outerspaces.assessed_value_history_subset` | 198 |
| `warehouse.assessed_value_history` | Tier 1 parcels | `source_outerspaces.assessed_value_history_subset` | 3,687 |
| `warehouse.permit_history` | Tier 0 parcels | `source_outerspaces.permit_history_subset` | 60 |
| `warehouse.permit_history` | Tier 1 parcels | `source_outerspaces.permit_history_subset` | 1,383 |
| `warehouse.availability_signal` | Tier 0 buildings | `source_outerspaces.availability_signal` | 2 |
| `warehouse.availability_signal` | Tier 1 buildings | `source_outerspaces.availability_signal` | 49 |
| `raw.seattle_building_energy_benchmarking` | Tier 0 parcels | `source_outerspaces.seattle_building_energy_benchmarking_subset` | 20 |
| `raw.seattle_building_energy_benchmarking` | Tier 1 parcels | `source_outerspaces.seattle_building_energy_benchmarking_subset` | 319 |
| `private.jll_building_availability_match` | Tier 0 buildings, reviewed only | `source_outerspaces.jll_building_availability_match_reviewed` | up to 1 |
| `private.jll_building_availability_match` | Tier 1 buildings, reviewed only | `source_outerspaces.jll_building_availability_match_reviewed` | up to 20 |
| `private.build_vitals_distress_seed_match` | Tier 0 buildings, reviewed only | `source_outerspaces.distress_seed_match_reviewed` | up to 1 |
| `private.build_vitals_distress_seed_match` | Tier 1 buildings, reviewed only | `source_outerspaces.distress_seed_match_reviewed` | up to 5 |
| `raw.building_shapefile_features` | TBD join/filter | `source_outerspaces.building_footprint_features_subset` | TBD |

Counts are additive only after de-duplication. Tier 0 buildings may also satisfy the Tier 1 filter; loading logic should use upsert/deduplicate by stable IDs.

## Exact Tier 0 Building IDs

Use exact IDs, not loose address matching:

| Role | Building | `building_id` | `source_parcel_id` |
| --- | --- | --- | --- |
| Hero | 2601 Elliott Ave | `king_county_wa:0653000250:profile` | `0653000250` |
| Backup | Gibraltar Tower / 1518 3rd Ave | `king_county_wa:1975700380:profile` | `1975700380` |
| Contrast | 1918 8th Ave | `king_county_wa:0660000650:profile` | `0660000650` |
| VSS test | 1700 Westlake Ave N | `king_county_wa:4088803750:profile` | `4088803750` |

## Tier 1 Selection Recommendation

Recommended Tier 1 definition:

```sql
WITH base AS (
  SELECT *
  FROM warehouse.building_profile_with_coords
  WHERE city = 'Seattle'
    AND state = 'WA'
    AND parcel_centroid_lat BETWEEN 47.58 AND 47.66
    AND parcel_centroid_lon BETWEEN -122.37 AND -122.30
    AND parcel_centroid_lat IS NOT NULL
    AND parcel_centroid_lon IS NOT NULL
), office_base AS (
  SELECT *
  FROM base
  WHERE (
      lower(coalesce(asset_class, '')) = 'office'
      OR lower(coalesce(source_use_description, '')) LIKE '%office%'
    )
    AND coalesce(gross_sf, 0) >= 25000
), tier1_buildings AS (
  SELECT DISTINCT b.building_id, b.source_parcel_id
  FROM office_base b
  LEFT JOIN warehouse.availability_signal a
    ON a.building_id = b.building_id
  WHERE coalesce(b.peak_to_current_compression_pct, 0) <= -25
     OR a.building_id IS NOT NULL
     OR coalesce(b.permit_count, 0) = 0
     OR b.latest_permit_date < DATE '2019-01-01'
)
SELECT *
FROM tier1_buildings;
```

Why this is the right starting set:

- It is reproducible.
- It is centered on the Demo 1 geography.
- It includes the asset class where office distress/recast is most legible.
- It requires scale large enough to matter visually.
- It catches both distress and future-proofing candidates:
  - value compression;
  - availability;
  - permit silence;
  - aging/no recent investment signal.
- It avoids copying the entire Seattle or King County warehouse.

Approximate dependent row counts:

| Relation | Tier 1 rows |
| --- | ---: |
| buildings | 67 |
| parcels | 67 |
| raw parcel rows | 67 |
| assessed value history | 3,687 |
| permit history | 1,383 |
| availability signal | 49 |
| energy benchmarking | 319 |
| private JLL matches | up to 20, review-gated |
| private distress seed matches | up to 5, review-gated |

## AVAILABLE / DERIVED / MISSING Analysis

### AVAILABLE

- building identity;
- address/PIN/building ID;
- office/use classification;
- gross square feet;
- stories and year built where populated;
- owner proxy;
- current/peak assessed value;
- value-compression summary;
- assessed-value history;
- permit history;
- availability signal where populated;
- private JLL availability rows/matches, review-gated;
- private seed distress rows/matches, review-gated;
- parcel zoning/present-use/site-constraint fields;
- Seattle energy benchmarking rows by parcel where available;
- centroid coordinates.

### DERIVED

- As-Is state;
- trajectory state;
- healthy/vulnerable/distressed/severely-distressed label;
- Recast Opportunity state;
- future-use options;
- physical/regulatory/market/financial/incentive fit;
- attention-layer ranking;
- Recast recommendation;
- VSS evidence claims;
- capital-stack program fit.

### MISSING OR NOT YET VERIFIED

- verified debt maturity;
- verified foreclosure/receivership/court data as a structured source;
- verified whole-building vacancy across the city;
- tenant/lease demand beyond availability proxies;
- exact conversion costs;
- structural/MEP/code feasibility;
- complete interior plans for the hero building;
- verified building-footprint join from `raw.building_shapefile_features` to Recast `building_id`;
- local VSS "Ctrl-F for a building" result data;
- verified incentive eligibility for a specific building/project.

## Refresh Strategy

| Dataset | Refresh strategy for Demo 1 | Later product strategy |
| --- | --- | --- |
| market/taxonomy | static snapshot | occasional sync |
| building profile subset | static snapshot | periodic sync |
| parcel/raw parcel subset | static snapshot | periodic sync |
| value history subset | static snapshot | periodic sync |
| permit history subset | static snapshot | periodic sync |
| availability signal | static snapshot | periodic sync when source updates |
| Seattle energy benchmarking | static snapshot | annual/periodic sync |
| private JLL rows | static reviewed snapshot only | licensed/reviewed pipeline |
| private distress seed | static reviewed snapshot only | replace with verified source tables |
| VSS evidence | generated locally | generated per capture session |
| Recast assessments | generated locally | generated per run and stored with lineage |
| capital program reference | curated static seed | periodic authoritative refresh |

## Privacy / Licensing / Provenance Notes

- Keep Outerspaces as upstream provenance; local database is `recast`, not `outerspaces`.
- Public assessor/permit/energy data can support visible claims, but cite source lineage.
- Private JLL rows are review/licensing-gated. Do not show raw payloads or extraction text unless rights are confirmed.
- Distress seed rows are unverified. Do not promote seed claims as facts.
- Do not write raw private video into source schemas.
- Do not store credentials, Supabase URLs with secrets, presigned URLs, camera paths, or API tokens in the local database manifest.
- Keep VSS observations separate from records data: a walkthrough clip supports physical observations, not whole-building utilization corrections.

## Validation Queries After Eventual Loading

Run these only after Peter approves data movement and the local database exists.

### Row Counts

```sql
SELECT count(*) FROM source_outerspaces.building_profile_with_coords_subset;
SELECT count(*) FROM source_outerspaces.parcel_subset;
SELECT count(*) FROM source_outerspaces.assessed_value_history_subset;
SELECT count(*) FROM source_outerspaces.permit_history_subset;
SELECT count(*) FROM source_outerspaces.availability_signal;
SELECT count(*) FROM source_outerspaces.seattle_building_energy_benchmarking_subset;
```

Expected initial counts before de-duplication:

```text
Tier 0 buildings: 4
Tier 1 buildings: 67
Tier 0 assessed value rows: 198
Tier 1 assessed value rows: 3,687
Tier 0 permit rows: 60
Tier 1 permit rows: 1,383
Tier 0 availability rows: 2
Tier 1 availability rows: 49
Tier 0 energy rows: 20
Tier 1 energy rows: 319
```

### Hero Building Presence

```sql
SELECT building_id, source_parcel_id, address
FROM source_outerspaces.building_profile_with_coords_subset
WHERE building_id IN (
  'king_county_wa:0653000250:profile',
  'king_county_wa:1975700380:profile',
  'king_county_wa:0660000650:profile',
  'king_county_wa:4088803750:profile'
)
ORDER BY address;
```

### Join Health

```sql
SELECT b.building_id, count(v.*) AS value_rows
FROM source_outerspaces.building_profile_with_coords_subset b
LEFT JOIN source_outerspaces.assessed_value_history_subset v
  ON v.source_parcel_id = b.source_parcel_id
GROUP BY b.building_id
ORDER BY b.building_id;

SELECT b.building_id, count(p.*) AS permit_rows
FROM source_outerspaces.building_profile_with_coords_subset b
LEFT JOIN source_outerspaces.permit_history_subset p
  ON p.source_parcel_id = b.source_parcel_id
GROUP BY b.building_id
ORDER BY b.building_id;
```

### Source / Derived Boundary

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema = 'source_outerspaces'
  AND table_name ILIKE '%assessment%';
```

Expected: no rows. Recast assessments belong in `recast`, not `source_outerspaces`.

## Tables / Data Not To Copy Locally

Do not copy these for Demo 1:

- full Supabase backups;
- full `public.kingcounty_raw_value_history_full` with 27,557,132 rows;
- full `warehouse.assessed_value_history`;
- full `warehouse.permit_history`;
- full `public.kingcounty_raw_parcel`;
- full raw city/public datasets unrelated to Demo 1;
- full `warehouse.owner_portfolio_insight` with 221,580 rows;
- raw JLL payloads/extraction text unless rights are confirmed;
- unreviewed private seed data as final truth;
- raw private video;
- NGC/model caches in Postgres;
- VSS Docker volumes;
- local filesystem paths;
- credentials or connection strings with secrets.

Also do not copy NYC/San Francisco warehouse/raw tables for Demo 1. They exist in Outerspaces but are not part of this Seattle Recast demo scope.

## Recommended Movement Sequence

Do not execute this until approved.

1. Create local PostgreSQL database `recast` on the GB100.
2. Create schemas: `source_outerspaces`, `recast`, `vss`, `capital`, `meta`.
3. Create `meta.source_export_manifest` and `meta.load_run` first.
4. Load support tables:
   - `source_outerspaces.market`;
   - `source_outerspaces.asset_class_taxonomy`.
5. Load Tier 0 source subsets.
6. Validate Tier 0 row counts and hero-building joins.
7. Build minimal derived `recast.building`, `recast.building_signal_snapshot`, and `recast.building_attention_candidate`.
8. Load Tier 1 source subsets.
9. Validate Tier 1 counts and attention-layer candidates.
10. Add `capital.program` curated records from authoritative public sources.
11. Add empty/generated-local `vss` tables.
12. Run one Recast assessment generation for 2601 Elliott only.
13. Stop and validate Demo 1 before considering Tier 2.

## First Data Movement Step After Approval

After Peter approves this manifest, the first real movement should be:

```text
Tier 0 source-only load into source_outerspaces
```

That means only:

- 4 building profile rows;
- 4 parcel rows;
- 4 raw parcel rows;
- 198 assessed-value-history rows;
- 60 permit-history rows;
- 2 availability rows;
- 20 energy benchmarking rows;
- reviewed private rows only if explicitly approved.

Then validate all joins before loading Tier 1.
