# Acceptance Checklist

## Judge-Visible Acceptance

Within 90 seconds, a judge can answer:

- [ ] How is this building doing today, and where is it heading?
- [ ] What could this building become?
- [ ] What could make that transformation economically possible?
- [ ] What did NVIDIA see that records could not?
- [ ] Why should anyone believe the recommendation?

If any answer is unclear, the demo is not ready.

## Recon

- [ ] Queryable As-Is / trajectory / distress signals are listed with source, join key, coverage, freshness, and gaps.
- [ ] The five named hero candidates have been compared deeply.
- [ ] #1 hero and #2 backup are selected.
- [ ] One credible future-proofing contrast candidate is identified or explicitly rejected.
- [ ] The hero building is selected from evidence, not convenience.
- [ ] `1700 Westlake` is treated as a VSS test building unless its distress story is proven.
- [ ] Planned/possible datasets are not described as existing.
- [ ] Actual incentive/capital-stack programs are sourced from authoritative pages.

## Data / Architecture

- [ ] One building context packet exists.
- [ ] The packet includes address, parcel/PIN, footprint, records snapshot, and selected permit context.
- [ ] Citywide/district 3D building packet exists.
- [ ] One known-good video source or clip exists.
- [ ] One VSS output JSON exists.
- [ ] Supabase is documented as source of truth.
- [ ] Acer GN100 is documented as local inference/evidence engine.
- [ ] No plan requires full Supabase replication onto Acer.
- [ ] Raw private video is not written back to Supabase.

## Acer / VSS

- [ ] Acer is reachable on the network.
- [ ] VSS base profile is healthy on `DGX-SPARK`.
- [ ] One walkthrough/source/clip is registered.
- [ ] At least three Ctrl-F style queries return relevant timestamps/clips.
- [ ] Failure modes are documented for bad searches.
- [ ] VSS evidence is used to support or limit a reuse hypothesis.
- [ ] If VSS Ctrl-F is not proven, the demo does not imply it works.

## 3D Visualization

- [ ] Seattle/downtown starts from above.
- [ ] Candidate buildings emerge visibly.
- [ ] Insufficient-evidence buildings are gray.
- [ ] Hero building zoom-in works.
- [ ] Zoom back to city scale works.
- [ ] Interior twin is either clearly useful or explicitly deferred.

## Recast Assessment

- [ ] Records signal and physical evidence remain separate.
- [ ] Claims have evidence labels.
- [ ] Limitations are shown.
- [ ] `INSUFFICIENT_EVIDENCE` appears when appropriate.
- [ ] Top reuse recommendation has factors, risks, and unknowns.
- [ ] Potential capital-stack / incentive support is shown.
- [ ] Verified eligibility is distinguished from potential relevance.
- [ ] No final feasibility/code/cost claim is made.

## Demo

- [ ] The judge sees Seattle -> building intelligence -> As-Is/trajectory -> physical evidence -> VSS -> alternative futures -> capital-stack support -> best path -> city scale.
- [ ] The citywide opening shows both the distressed hero and a still-working/vulnerable contrast.
- [ ] There is a recorded fallback.
- [ ] There is a cached output fallback.
- [ ] The 2-3 minute script has been rehearsed.
