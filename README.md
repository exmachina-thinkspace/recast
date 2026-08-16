# Recast

Recast is a building intelligence system for understanding what a building is today, where it is heading, what it could become, and what could make that transformation economically possible.

For Demo 1, Recast combines city/property data, building records, financial and availability signals, NVIDIA VSS physical understanding, and capital-stack / incentive intelligence into one workflow:

```text
Seattle
  -> Building Intelligence Layer
  -> As-Is
  -> Trajectory
  -> Recast Opportunity
  -> Physical Understanding via NVIDIA VSS
  -> Alternative Futures
  -> Capital Stack / Incentive Intelligence
  -> Best Path Forward
  -> City Scale
```

The demo uses a distressed or vulnerable building as the extreme case, then shows the larger thesis: Recast does not have to wait for a building to fail. The same intelligence layer can help owners act while their buildings still have choices.

## Repository Status

This is the active Recast product/demo repo.

Historical research and broad hackathon preparation live in `city-of-seattle-prep`. This repo contains the curated, judge-facing Recast material.

## Quick Links

| Area | Path |
| --- | --- |
| Demo 1 plan | [`docs/demo1/`](docs/demo1/) |
| Pitch scripts | [`docs/pitch/`](docs/pitch/) |
| Local Recast data plan | [`docs/data/`](docs/data/) |
| VSS / live video ingestion | [`docs/vss/`](docs/vss/) |
| Mobile capture / recast-ios | [`docs/mobile/`](docs/mobile/) |
| Recast Lens Compound Engineering plan | [`docs/plans/recast-lens-compound-engineering-plan.md`](docs/plans/recast-lens-compound-engineering-plan.md) |
| Recast webapp Compound Engineering plan | [`docs/plans/recast-webapp-compound-engineering-plan.md`](docs/plans/recast-webapp-compound-engineering-plan.md) |
| Screenshots and sample outputs | [`assets/`](assets/) |
| Citywide 3D view (app prototype) | [`apps/city-view-3d/`](apps/city-view-3d/) |
| Image generation: "Alternative Futures" renders (FLUX.1-dev NIM on the Spark, hosted fallback) | [`services/image-gen/`](services/image-gen/) |
| Recast Lens bridge | [`services/recast-lens-bridge/`](services/recast-lens-bridge/) |
| Plans (Compound Engineering) | [`docs/plans/`](docs/plans/) |
| Documented solutions / learnings | [`docs/solutions/`](docs/solutions/) |

## Current Proof Points

- Demo 1 thesis and acceptance criteria are drafted.
- GB100 local PostgreSQL `recast` foundation is live with Tier 0 + Tier 1 public Outerspaces data loaded directly from Supabase and validated.
- iPhone live video ingestion has been verified through a Larix-style path: mobile H.264 video push -> MediaMTX -> RTSP -> Acer GN100 / VIOS.
- Recast-owned browser-camera capture is now started as `Recast Lens v1`: iPhone browser camera -> Recast frontend -> Recast Lens bridge on port `8910` -> latest frame/status for future VSS adapter.
- NVIDIA VSS semantic "Ctrl-F for a building" remains a gating experiment until live or recorded clip query results are verified.
- Capital-stack intelligence is included as a product layer, but specific building/program eligibility must remain explicitly classified as verified, potentially relevant, unknown, or not eligible.

## Seattle CRE Distress - OuterSpaces Findings

Last verified: 2026-08-16 against the full OuterSpaces Supabase database.

These findings were calculated from OuterSpaces, not from the small GB100/local `recast` subset database. The local Recast database is a curated prototype working set and should not be used to determine the full data inventory or market-scale findings.

### Source Of Truth

Primary calculation tables:

- `warehouse.building_profile_with_coords`
- `warehouse.availability_signal`

Relevant supporting OuterSpaces tables for context and reproducibility:

- `warehouse.assessed_value_history`
- `warehouse.permit_history`
- `warehouse.sale_history`
- `public.kingcounty_raw_parcel`
- `public.kingcounty_raw_value_history`
- `public.kingcounty_staging_collective_levy_rates`

The headline compound-distress calculation used `warehouse.building_profile_with_coords` joined to the latest `warehouse.availability_signal` row by `building_id`. The King County raw/staging tables support the separate tax-base analysis and broader source-data provenance; they were not directly summed in the compound-distress calculation.

Supporting research from the prep repo:

- `/Users/peterchee/.openclaw/workspace-dev-ava/city-of-seattle-prep/research/seattle-property-tax/methodology/existing-data-recon.md`
- `/Users/peterchee/.openclaw/workspace-dev-ava/city-of-seattle-prep/research/seattle-property-tax/analysis/seattle-office-property-tax-2019-2026.md`

### Headline Compound-Distress Finding

Calculated from OuterSpaces:

- 47 Seattle office buildings have an active availability signal and at least 25% assessed-value compression.
- Those buildings represent 6,892,769 SF available.
- Those buildings represent 28,338,005 gross SF.
- Peak assessed value: $11.745B.
- Current assessed value: $5.508B.
- Peak-to-current assessed-value decline: $6.238B.
- 43 of the 47 buildings are concentrated in the downtown/SLU bounding area.
- The downtown/SLU concentration represents 6.43M available SF.
- The downtown/SLU concentration represents $6.09B of assessed-value decline.

This is a derived OuterSpaces finding, not an externally reported market statistic.

Current defensible pitch fact:

> $6.2 billion in assessed value has been erased from just 47 Seattle office buildings - buildings representing nearly 7 million square feet of available space.

Precise caveat: this means available square footage represented by buildings that meet the compound criteria. Do not describe it as 7 million square feet of total vacant Seattle office space.

### Citywide Value Compression

Calculated from OuterSpaces office-like buildings:

- Seattle office-like current assessed value: $22.21B.
- Individual-property peak assessed value: $41.20B.
- Peak-to-current assessed-value compression: $18.99B.
- Downtown/SLU portion of that compression: $16.24B.

Methodological caveat: peak-to-current uses each property's individual assessed-value peak. It is not a fixed-year comparison from a single common starting year.

### Tax-Base Finding

Existing OuterSpaces/King County tax-base analysis found:

- 2019 Seattle office assessed/taxable value: $27.894B.
- 2026 Seattle office assessed/taxable value: $22.009B.
- Decline: $5.885B / 21.10%.
- Office share of Seattle assessed property base: 33.7% -> 24.1%.
- Modeled burden shift away from office toward non-office property: approximately $86.4M.

The $86.4M figure is a modeled allocation/burden shift. It is not actual individual tax bills, taxes paid, lost City revenue, or a homeowner-specific tax finding.

### Methodology

Headline compound-distress filter:

- Geography: `city = 'Seattle'` and `state = 'WA'`.
- Use: `asset_class = 'Office'`.
- Availability: latest `warehouse.availability_signal` row joined by `building_id`.
- Compression: `peak_to_current_compression_pct <= -0.25`.
- Available SF: sum `availability_signal.availability_sf`.
- Assessed-value decline: sum `peak_assessed_value - current_assessed_value`.
- Downtown/SLU bounding area: latitude 47.58-47.64 and longitude -122.36 to -122.30.

Date-selection/deduplication assumption: availability was deduped by taking the latest `last_observed` row per `building_id` where an availability signal exists.

### Caveats

- `availability_signal` coverage is sparse.
- 6.9M SF is the available square footage represented by buildings meeting the compound criteria, not total Seattle vacancy.
- Do not describe 6.9M SF as Seattle's total vacant office space.
- Assessed value is not the same as market value.
- Peak-to-current is not a fixed-year comparison.
- Recast does not currently have a clean building-level occupancy time series.
- Recast does not currently have a complete verified debt/default/foreclosure dataset.
- Tax burden shift is modeled rather than observed individual tax bills.

### Why This Matters To Recast

Recast is not simply reporting that the Seattle office market is distressed. OuterSpaces lets Recast identify individual buildings where multiple distress signals overlap, then evaluate those buildings as candidates for reuse.

That compound signal is the difference between quoting a market report and creating building-level intelligence: Recast can combine property records, value trajectory, availability, permits, sales, zoning/context, physical evidence, and capital-stack possibilities into an evidence-backed recommendation.

## Demo 1 Judge Questions

Within 90 seconds, the demo should make these answers obvious:

1. Which building deserves attention?
2. How is it doing today, and where is it heading?
3. What did NVIDIA VSS see that records could not?
4. What could the building become?
5. What could make that transformation economically possible?
6. Why should the recommendation be believed?

## Product Principle

Do not overclaim. Every claim should be marked as one of:

- `KNOWN`
- `OBSERVED`
- `INFERRED`
- `UNKNOWN`
- `INSUFFICIENT_EVIDENCE`

The point is not to pretend Recast already knows everything. The point is to show that Recast can assemble the evidence needed to move from building records to physical understanding to an actionable investment thesis.
