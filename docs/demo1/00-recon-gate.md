# Recon Gate Before Implementation

Do not start broad engineering until these questions are answered with evidence.

## A. As-Is Intelligence

Using existing Outerspaces/Supabase datasets, answer:

- What can we actually know about how a building is performing today?
- What can we know about its trajectory?
- Which indicators are leading versus lagging?
- Can we distinguish healthy, vulnerable, distressed, and severely distressed buildings with defensible evidence?

Show exactly which building-level datasets/signals are actually present and queryable today.

Acceptable answer format:

```text
Signal
Dataset/table/source
Join key
Coverage
Freshness
Example building rows
Known gaps
```

Do not describe planned or possible datasets as existing.

## B. Recast Intelligence

For one building, answer:

- What existing data helps determine alternative-use potential?
- What critical information is missing?
- What can VSS add that records cannot?
- Which alternative uses can we credibly compare?

## C. Incentive / Capital Stack Intelligence

Perform focused recon of actual government/public programs relevant to Seattle adaptive reuse, retrofit, repositioning, affordable housing, energy performance, and building reinvestment.

For every relevant program, capture:

```text
program
agency
government level
eligible building/use
geographic eligibility
project requirements
available funding
grant / credit / loan / incentive
application timing
matching requirement
stackability
expiration
source
confidence
```

Do not assume programs exist or apply. Never claim a building qualifies unless the program requirements support that conclusion.

## D. Hero Building Candidates

Produce 3-5 real candidate distressed buildings from the available signals.

Each candidate needs:

- address;
- parcel/PIN or stable building ID;
- distress signals;
- evidence sources;
- why it could make a good story;
- reuse hypothesis;
- potential capital-stack/incentive relevance;
- known missing evidence.

The best hero is not necessarily the most distressed building. It may be the building with the most dramatic gap between weak As-Is performance and compelling Recast opportunity, especially where incentives could materially change feasibility.

## E. VSS Ctrl-F Test

Test whether VSS can act like Ctrl-F for a building walkthrough:

```text
natural-language search -> relevant timestamp/clip
```

Test queries:

- Show me empty or underutilized areas.
- Find large open floorplates.
- Show loading access or service access.
- Show evidence of low utilization.
- Find mechanical/electrical/service rooms if present.
- Find areas that look suitable or unsuitable for residential conversion.

The required output is not a plan. It is:

```text
query
result timestamp/clip
quality judgment
failure mode if bad
```

## F. Citywide 3D vs. Interior Twin

Evaluate separately:

- citywide 3D visualization for the Seattle distress flyover;
- interior digital twin for building evidence anchoring.

Citywide 3D is central to the story. Interior twin is optional unless it materially improves the wow moment.

## G. Reuse Reasoning Support

Determine whether existing building/zoning/property data plus VSS observations can support alternative-use reasoning.

The demo must end with:

```text
This building could become this, because...
```

Supported outputs:

- plausible use rankings;
- evidence-backed pros/risks;
- explicit unknowns;
- no recommendation / insufficient evidence when appropriate.

Unsupported outputs:

- final feasibility;
- code compliance;
- construction-cost certainty;
- engineering certification.

## 6. Records Signal + Physical Evidence

Replace sensor-corrected scoring language with:

```text
Records Signal
Physical Evidence
Recast Assessment
```

A single camera, walkthrough, or short time interval cannot correct whole-building occupancy.

## 7. Judge-Visible Readiness

Within 90 seconds, a judge must understand:

1. How is this building doing today, and where is it heading?
2. What could this building become?
3. What could make that transformation economically possible?
4. What did NVIDIA see that records could not?
5. Why should anyone believe the recommendation?

If any answer is not obvious, the demo is not ready.
