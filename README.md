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
| Screenshots and sample outputs | [`assets/`](assets/) |
| Citywide 3D view (app prototype) | [`apps/city-view-3d/`](apps/city-view-3d/) |
| Plans (Compound Engineering) | [`docs/plans/`](docs/plans/) |
| Documented solutions / learnings | [`docs/solutions/`](docs/solutions/) |

## Current Proof Points

- Demo 1 thesis and acceptance criteria are drafted.
- GB100 local PostgreSQL `recast` foundation is live with Tier 0 + Tier 1 public Outerspaces data loaded directly from Supabase and validated.
- iPhone live video ingestion has been verified through a Larix-style path: mobile H.264 video push -> MediaMTX -> RTSP -> Acer GN100 / VIOS.
- NVIDIA VSS semantic "Ctrl-F for a building" remains a gating experiment until live or recorded clip query results are verified.
- Capital-stack intelligence is included as a product layer, but specific building/program eligibility must remain explicitly classified as verified, potentially relevant, unknown, or not eligible.

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
