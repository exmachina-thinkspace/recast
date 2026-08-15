# Recon-Gated Build Sequence

## Phase 0: Recon Before Implementation

1. Complete [00-recon-gate.md](00-recon-gate.md).
2. Confirm exactly which As-Is, trajectory, distress, and Recast-opportunity signals are queryable today.
3. Produce 3-5 real hero candidates.
4. Test VSS Ctrl-F behavior on walkthrough/video.
5. Recon actual public incentive/capital-stack programs.
6. Decide whether interior twin work is worth the time.
7. Confirm the Recast hero building separately from the VSS test building.

## Phase 1: Distress Candidate Layer

1. Identify candidate building set.
2. Pull footprint geometry for citywide 3D.
3. Pull verified As-Is / trajectory / distress signals.
4. Calculate or stage evidence-backed state labels, not arbitrary scores.
5. Mark insufficient-evidence buildings gray.
6. Export a bounded city packet:

   ```text
   demo1/data/city-recast-candidates.json
   ```

7. Do not export full citywide tables unless offline mode requires a bounded subset.

## Phase 2: Hero Building Evidence

1. Select the strongest hero building from recon: the biggest gap between current trajectory and plausible future.
2. Pull parcel/PIN/address/footprint.
3. Pull value, permit, zoning, availability, and distress evidence.
4. Pull or stage available photos/video/plans.
5. Pull potentially relevant incentive/program facts.
6. Export a compact hero packet:

   ```text
   demo1/data/hero-building-context.json
   ```

## Phase 3: Acer GN100 / VSS Ctrl-F

1. Confirm Acer readiness using `acer-gn100/` runbooks.
2. Confirm VSS base profile on `DGX-SPARK`.
3. Register the hero walkthrough/clip if available, otherwise the `1700 Westlake` test source.
4. Run semantic building-search prompts.
5. Save results and failures:

   ```text
   demo1/data/vss-ctrl-f-results.json
   ```

## Phase 4: Citywide 3D

1. Build Seattle/downtown flyover view.
2. Render gray insufficient-evidence buildings.
3. Highlight candidate buildings.
4. Zoom into the hero building.
5. Zoom back out after the recommendation.

## Phase 5: Recast Recommendation

1. Normalize records signals and VSS outputs into evidence claims.
2. Preserve `KNOWN / OBSERVED / INFERRED / UNKNOWN / INSUFFICIENT_EVIDENCE`.
3. Build As-Is and trajectory summary.
4. Rank 2-3 plausible alternative futures.
5. Add potential capital-stack / incentive support.
6. Show why the top path wins.
7. Show risks and unknowns.
8. Store only evidence pointers, not private raw video.

## Phase 6: Demo UI

1. Start above Seattle.
2. Reveal early-warning distressed buildings.
3. Select hero building.
4. Explain why Recast flagged it.
5. Enter physical evidence and run/show VSS search.
6. Present alternative futures.
7. Show capital-stack / incentive support.
8. Explain why and what remains unknown.
9. Zoom back to city scale.

## Phase 7: Hardening / Fallbacks

1. Prepare recorded fallback clip.
2. Prepare cached VSS output.
3. Prepare local JSON fallback if Supabase/network fails.
4. Prepare screenshot/video backup of the full flow.
5. Rehearse the 2-3 minute version.

## Stop Conditions

Stop adding features when:

- 3-5 real hero candidates exist;
- one hero building has a credible evidence-backed story;
- at least three VSS Ctrl-F queries return relevant timestamps/clips or documented failures;
- Recast can answer As-Is, what the hero building could become, and what could help economics;
- unknowns are visible.

More architecture is useful only after the magic is proven.
