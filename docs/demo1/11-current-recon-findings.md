# Current Recon Findings

Generated: 2026-08-15.

Status: recon snapshot, not implementation approval.

## Existing Outerspaces Data Checked

Live read-only schema/count check against Outerspaces Supabase found:

| Layer | Relation | Count | Demo meaning |
| --- | --- | ---: | --- |
| Building spine + coordinates | `warehouse.building_profile_with_coords` | 1,112,066 | Strong map/profile substrate with asset class, gross SF, stories, year built, sales, value compression, permits, coords, signal flags |
| Availability signal | `warehouse.availability_signal` | 63 | Small public/derived availability layer; useful but not enough alone |
| Assessed value history | `warehouse.assessed_value_history` | 10,839,791 | Strong trajectory/value-compression input |
| Permit history | `warehouse.permit_history` | 2,977,869 | Investment/activity proxy |
| Private JLL availability raw | `private.jll_building_availability_raw` | 124 | Private market availability signal; review-gated |
| Private JLL match | `private.jll_building_availability_match` | 124 | 96 matched per local recon; still review-gated |
| Private distress seed raw | `private.build_vitals_distress_seed_raw` | 25 | Human-curated seed queue; all rows are unverified |
| Private distress seed match | `private.build_vitals_distress_seed_match` | 25 | 16 deterministic matches per local recon; review-gated |
| Full King County value history | `public.kingcounty_raw_value_history_full` | 27,557,132 | Broad history available; use with existing caveats |

## What As-Is Intelligence Can Support This Weekend

Supported now:

- parcel/building identity and coordinates;
- asset class, gross/net SF, stories, age/year built where populated;
- assessed-value trajectory and value compression;
- permit/investment activity;
- availability/vacancy candidates from public/derived and private review-gated layers;
- seed distress candidates for review, not final claims;
- citywide/district 3D candidate visualization from footprints/coords.

Not yet proven or not yet loaded as durable facts:

- building energy benchmarking joined into the Recast layer;
- current physical condition except where VSS/video is captured;
- tenant/lease demand beyond availability proxies;
- verified debt maturity;
- bulk court/receivership records;
- exact conversion costs;
- structural/MEP/code feasibility.

Leading-ish indicators available now:

- availability/leased percentage where sourced;
- permit silence or investment activity;
- purchase-basis/value compression;
- unverified seed signals pending source review.

Lagging indicators available now:

- assessed value decline;
- sale history;
- permit history after filings occur.

## Candidate Hero Building Signals

Current candidates from private seed and availability layers are not final truth. They are queues for source review.

Examples surfaced by the seed/availability checks:

- `1000 2nd Ave` / `1000 Second`: seed claims receivership, debt delinquency, large availability; matched; source review needed.
- `400 Westlake Ave N`: seed claims loan default, lender takeover, extreme vacancy; matched; source review needed.
- `1015 2nd Ave` / Federal Reserve Building: seed claims lender takeover, extreme vacancy, adaptive reuse; matched; source review needed.
- `1518 3rd Ave` / Gibraltar Tower: seed claims foreclosure/lender ownership/extreme vacancy; matched; source review needed.
- `2601 Elliott Ave`: availability layer shows very high available percentage and severe value compression; also relevant to Seattle office-to-residential public context; needs exact story review.

Hero choice should favor the strongest gap between weak As-Is trajectory and credible Recast opportunity, not merely the highest distress label.

## Five-Candidate Deep Recon

### 1. 2601 Elliott Ave - Recommended #1 Hero

As-Is:

- Outerspaces profile: office, `350,310` gross SF, 6 stories, year built `1916`, zoning `DH2/75`.
- Outerspaces value trajectory: current assessed value `$49.739M`, peak `$159.416M`, peak-to-current compression `-68.8%`.
- Outerspaces availability signal: `345,000` available SF, `98.48%` availability, medium confidence, last observed `2026-06-19`.
- Public reporting says the former Zulily building was all or mostly empty, changed hands through a foreclosure sale, and Vanbarton/Gensler planned residential conversion.
- Seattle Office-to-Residential page says `2601 Elliott Avenue` has conditional approval and is expected to deliver `260` residential units, including `26` affordable units.

Trajectory:

```text
DISTRESSED / DETERIORATING, with unusually strong Recast evidence.
```

Recast opportunity:

- #1: residential / mixed-income conversion.
- #2: affordable-housing-enhanced residential conversion.
- #3: legacy office/lab repositioning is weaker because the public story has already moved toward residential.

Capital-stack opportunity:

- Seattle Office-to-Residential tax deferral: `POTENTIALLY RELEVANT`, with city conditional approval as strong program-path evidence.
- Seattle City Light energy incentives: `POTENTIALLY RELEVANT` for retrofit measures.
- King County C-PACER: `POTENTIALLY RELEVANT` for eligible energy/resilience improvements.
- Affordable housing capital: `POTENTIALLY RELEVANT IF PROJECT STRUCTURE QUALIFIES`, because 26 affordable units are part of the city-described plan.
- Federal Historic Tax Credit: `POTENTIALLY RELEVANT / UNKNOWN`; public reporting says landmark status may be sought, but eligibility is not verified.

Why it wins:

```text
It has the clearest full arc: obsolete office -> severe As-Is weakness -> real residential future -> city incentive path -> capital-stack surprise.
```

Main weakness:

- VSS physical evidence for 2601 is not yet in hand.

### 2. Gibraltar Tower / 1518 3rd Ave - Recommended #2 Backup

As-Is:

- Outerspaces profile: office, `59,400` gross SF, 8 stories, year built `1910`, zoning `DMC 240/290-440`.
- Outerspaces value trajectory: current assessed value `$10.551M`, peak `$13.102M`, compression `-19.47%`.
- Seed table claims foreclosure/lender ownership/extreme vacancy, but seed status remains unverified.
- DJC and CoStar reporting describe the building as mostly/vacant or vacant, foreclosure-linked, and moving toward artist housing / loft conversion.

Trajectory:

```text
DISTRESSED, with strong narrative but less database strength than 2601 Elliott.
```

Recast opportunity:

- #1: artist live/work housing or deed-restricted affordable ownership.
- #2: mixed studio/community/retail activation.
- #3: value-add office/creative reuse.

Capital-stack opportunity:

- Seattle Office-to-Residential tax deferral: `POTENTIALLY RELEVANT`.
- Affordable housing capital: `POTENTIALLY RELEVANT IF PROJECT STRUCTURE QUALIFIES`.
- Historic tax credit: `UNKNOWN`; age suggests possibility, but certified historic status is not verified.

Why it is backup:

```text
It may be the more surprising human story, but the loaded data story is less complete.
```

### 3. 400 Westlake Ave N

As-Is:

- Outerspaces profile: office, `268,005` gross SF, 15 stories, year built `2022`, zoning `SM-SLU 175/85-280`.
- Outerspaces value compression: `-43.30%`.
- Private JLL extraction: `10.0%` leased, `204,131` available SF, `89.97%` availability, unreviewed but exact matched.
- Public reporting says Martin Selig defaulted on debt tied to 400 Westlake and the Federal Reserve Building, with potential transfer to lender.

Trajectory:

```text
DISTRESSED / DETERIORATING, but alternative future is less obvious.
```

Recast opportunity:

- #1: office repositioning / tenant strategy.
- #2: life-science or innovation-space repositioning if physical/program fit supports it.
- #3: residential conversion is not yet credible from current evidence.

Capital-stack opportunity:

- Energy / C-PACER: `POTENTIALLY RELEVANT`.
- Office-to-residential: `UNKNOWN / not enough evidence`.

### 4. Federal Reserve Building / 1015 2nd Ave

As-Is:

- Outerspaces profile: office, `232,045` gross SF, 11 stories, year built `1950`, zoning `DOC1 U/450/U`.
- Outerspaces value compression: `-46.15%`.
- Private JLL extraction: `39.6%` leased, `130,109` available SF, `60.36%` availability, unreviewed but exact matched.
- Public JLL listing shows current lease availability and describes a 1950 historic building renovated/expanded in 2021.
- Public reporting says Selig handed the Federal Reserve Building and 400 Westlake to lender Acore; this still needs official/legal confirmation before final claim.

Trajectory:

```text
VULNERABLE / DISTRESSED, with conflicting current-availability signals requiring review.
```

Recast opportunity:

- #1: specialized office / civic / institutional repositioning.
- #2: hospitality or residential only if physical/landmark/code evidence supports it.
- #3: keep/improve may be more credible because the building was recently renovated.

Capital-stack opportunity:

- Historic tax credit: likely already relevant historically, but future eligibility requires specific rehab status.
- Energy / utility incentives: `POTENTIALLY RELEVANT`.
- Office-to-residential: `UNKNOWN`.

### 5. 1000 Second Ave

As-Is:

- Outerspaces profile: office, `589,921` gross SF, 41 stories, year built `1986`, zoning `DOC1 U/450/U`.
- Outerspaces value compression: `-46.36%`.
- Public/derived availability layer says `12,597` available SF, `2.14%` availability, high confidence.
- Private JLL extraction conflicts: `70.8%` leased, `186,376` available SF, `31.59%` availability, unreviewed but exact matched.
- Seed claims receivership/debt delinquency/large availability, but seed status is unverified.

Trajectory:

```text
INSUFFICIENT EVIDENCE until availability and seed conflicts are resolved.
```

Recast opportunity:

- #1: keep/improve if low availability is true.
- #2: future-proofing / repositioning if value compression and private availability are true.
- #3: conversion requires more physical evidence.

Capital-stack opportunity:

- Energy / C-PACER: `POTENTIALLY RELEVANT`.
- Office-to-residential: `UNKNOWN`.

## Hero Ranking

| Rank | Candidate | As-Is evidence | Trajectory | Recast opportunity | VSS usefulness | Capital-stack opportunity | Data richness | Visual impact | Surprise |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2601 Elliott | Very strong | Very strong | Very strong | Needs physical media | Very strong | Strong | Strong | Strong |
| 2 | Gibraltar Tower | Medium | Strong from public reporting | Strong | Needs physical media | Medium/strong | Medium | Strong | Very strong |
| 3 | 400 Westlake | Strong | Strong | Medium | Needs physical media | Medium | Medium/strong | Strong | Medium |
| 4 | Federal Reserve Building | Medium/strong | Medium, conflicting signals | Medium | Needs physical media | Medium | Medium | Strong | Medium |
| 5 | 1000 Second | Conflicted | Unclear | Medium | Needs physical media | Medium | Conflicted | Strong | Low/medium |

Recommendation:

```text
#1 Hero: 2601 Elliott Ave
#2 Backup: Gibraltar Tower / 1518 3rd Ave
Stop searching for more hero candidates unless both fail source review or physical-evidence acquisition.
```

Candidate source pointers used in this pass:

- Outerspaces Supabase read-only checks for building facts, value compression, availability, permits, zoning/parcel context, JLL private extraction, and distress seed status.
- Seattle Office-to-Residential program page for 2601 Elliott conditional approval and units: https://www.seattle.gov/planning-and-community-development/office-to-residential
- DJC report on 2601 Elliott sale/conversion plan: https://www.djc.com/news/re/12170860.html
- JLL public listing for The Reserve Building / 1015 2nd Ave: https://property.jll.com/listings/federal-reserve-building-1015-2nd-ave-seattle-puget-sound
- Puget Sound Business Journal report on 400 Westlake / Federal Reserve lender-transfer risk: https://www.bizjournals.com/seattle/news/2024/12/03/selig-defaults-two-seattle-office-buildings.html
- DJC report on Gibraltar Tower artist-loft plan: https://www.djc.com/news/re/12175244.html
- CoStar report on Gibraltar Tower artist-housing plan: https://www.costar.com/article/1210483357/seattle-nonprofit-looks-to-join-national-office-conversion-wave-with-these-artist-lofts
- Martin Selig public listing for 1000 Second physical/floorplate context: https://martinselig.com/property/1000-second-avenue/

## Future-Proofing Contrast Candidate

Candidate:

```text
1918 8th Ave
```

Why:

- Outerspaces availability signal shows `0.47%` availability with high confidence.
- Outerspaces value compression is `-57.54%`.
- That is a useful 5-second contrast: the building may still look viable from occupancy/availability, while financial/property trajectory suggests vulnerability.

Classification:

```text
As-Is: currently viable signal
Trajectory: vulnerable / changing
Recast opportunity: emerging / not explored
```

Do not call it healthy without more evidence.

## VSS Gate Status

Current status:

```text
NOT PROVEN IN THIS PASS
```

What was checked:

- No current VSS operation tool is exposed in this session.
- No saved VSS Ctrl-F result artifact was found in the local prep/recon folders.
- Existing docs prove VSS suitability and setup planning, not the hands-on building Ctrl-F result.

Required before implementation:

| Query | Result required |
| --- | --- |
| Show me underutilized areas. | timestamp/clip, correctness, latency, Recast usefulness |
| Find large open spaces. | timestamp/clip, correctness, latency, Recast usefulness |
| Show me loading/service access. | timestamp/clip, correctness, latency, Recast usefulness |
| Find physical features relevant to conversion. | timestamp/clip, correctness, latency, Recast usefulness |
| Show evidence that supports or contradicts our hypothesis. | timestamp/clip, correctness, latency, Recast usefulness |

If VSS fails, redesign the demo moment around static physical evidence and do not imply NVIDIA retrieved clips semantically.

## Incentive / Capital Stack Recon

Authoritative programs found that could become a Recast intelligence layer:

| Program | Agency/level | Support type | Demo relevance | Eligibility posture | Source |
| --- | --- | --- | --- | --- | --- |
| Seattle Office-to-Residential Sales and Use Tax Deferral | City of Seattle / state-enabled local program | Tax deferral/exemption | Directly relevant to commercial-to-housing conversion | Potential only until building/use/project meets requirements | https://www.seattle.gov/planning-and-community-development/office-to-residential |
| Washington Clean Buildings Performance Standard grants/incentives | WA Commerce | Incentives/grants | Energy retrofit / compliance economics | Potential if covered building and program rules fit | https://www.commerce.wa.gov/cbps/cbps-grants-incentives/ |
| Seattle City Light business energy-efficiency incentives | Seattle City Light | Utility incentives | Retrofit / decarbonization economics | Potential; project measures and savings required | https://www.seattle.gov/city-light/business-solutions/large-commercial-and-industrial-business-solutions |
| King County C-PACER | King County | Private financing repaid via assessment | Energy/resiliency improvements for eligible commercial properties | Potential; project/property/lender requirements apply | https://kingcounty.gov/en/dept/dnrp/buildings-property/green-sustainable-building/building-decarbonization/cpacer |
| Seattle Office of Housing NOFA / Rental Housing Program | City of Seattle | Affordable housing capital funding | Affordable housing conversion or preservation paths | Potential; competitive and project-specific | https://www.seattle.gov/housing/funding-opportunities |
| Washington Housing Trust Fund / HOME / NHTF | WA Commerce / federal funds administered by state | Loans/grants | Affordable multifamily development/preservation | Potential; competitive and project-specific | https://www.commerce.wa.gov/multifamily-rental-housing/htf/ |
| King County Housing Finance Program | King County | Affordable housing funding | Countywide affordable housing development/preservation | Potential; competitive and project-specific | https://kingcounty.gov/en/dept/dchs/human-social-services/housing-homeless-services/funding-opportunities/housing-finance |
| Federal Historic Tax Credit | National Park Service / IRS, with WA DAHP interface | 20% rehab tax credit | Historic office/building rehab paths | Potential only for certified historic structures/rehab | https://dahp.wa.gov/grants-and-funding/federal-historic-tax-credit |
| Ecology brownfields / affordable housing cleanup grants | WA Ecology / EPA-linked | Grants/loans/technical assistance | Contaminated-site cleanup enabling reuse | Potential only if environmental/brownfield facts exist | https://ecology.wa.gov/spills-cleanup/contamination-cleanup/brownfields/affordable-housing |

Do not present any item as verified eligible without checking the specific building, use, project scope, timing, and application requirements.

## What Can Genuinely Be Demonstrated This Weekend

Likely demonstrable:

- Seattle/downtown building intelligence opening.
- Evidence-backed As-Is/trajectory panel using value, availability, permits, and reviewed seed facts.
- One hero building with a transparent evidence packet.
- VSS Ctrl-F for a building if hands-on VSS tests return relevant timestamps/clips.
- Alternative future comparison with explicit unknowns.
- Potential capital-stack support as a structured "programs to investigate" layer.

Should stay roadmap unless verified fast:

- mathematical As-Is/Recast scores;
- verified program eligibility for a specific building;
- full financial feasibility;
- code/structural/MEP feasibility;
- current whole-building utilization from one clip;
- automated court/debt-maturity monitoring;
- complete interior digital twin.

## Weekend Product Line

Use the zombie/distressed building as the extreme demo case, then reveal the broader thesis:

```text
Recast does not have to wait for a building to die.
The same intelligence layer can help owners future-proof buildings while they still have choices.
```
