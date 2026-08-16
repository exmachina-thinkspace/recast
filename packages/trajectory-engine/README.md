# Recast Trajectory Engine

An isolated, deterministic pipeline for reviewing a distressed building's 12-,
24-, and 36-month path. It turns reviewed building, BHI, debt, operating, lease,
tenant, and reuse inputs into auditable scenarios and evidence-gated action
recommendations.

This package is intentionally disconnected from the existing Recast backend.
It does not call an API, database, service, model, browser, or live data source,
and it does not alter the existing BHI calculation. Integration should happen
only after the contract and outputs have been reviewed.

## Pipeline

```text
reviewed facts + explicit assumptions
                |
                v
        evidence/shape gate
                |
                v
 current snapshot and exposure metrics
                |
                v
 improving / base / adverse scenarios
          at 12 / 24 / 36 months
                |
                +--> cash flow, DSCR, LTV, debt yield, reserves
                +--> debt maturity and refinance-gap proxy
                +--> lease rollover, concentration, renewal ranges
                +--> assumption-driven BHI path
                |
                v
 rule-based recommendations + reuse screens
                |
                v
       mandatory human review
```

Every factual value uses one of the existing Recast labels: `KNOWN`,
`OBSERVED`, `INFERRED`, `UNKNOWN`, or `INSUFFICIENT_EVIDENCE`. A usable value
must include a `source_ref`. If a necessary loan, operating, or lease fact is
unknown, the affected calculation returns `INSUFFICIENT_EVIDENCE`; the engine
does not fill the gap with a hidden default.

The included fixture is unmistakably marked `demo_only`; its output carries a
`DEMO_ONLY` review status and must never be presented as a real property result.

## Run the synthetic example

From the repository root:

```bash
python3 packages/trajectory-engine/run.py \
  --input packages/trajectory-engine/examples/distressed-office.synthetic.json \
  --output /tmp/recast-trajectory.json
```

Or run it as a module:

```bash
PYTHONPATH=packages/trajectory-engine \
  python3 -m trajectory_engine \
  --input packages/trajectory-engine/examples/distressed-office.synthetic.json
```

Run the standard-library tests:

```bash
PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s packages/trajectory-engine/tests -v
```

## Input boundaries

- `building`: current use, gross area, current market value, and optional
  acquisition context.
- `bhi`: current score and evidence coverage. Future BHI movement is an explicit
  scenario assumption, never an unexplained model output.
- `debt`: current balance, annual debt service, and maturity for each loan.
- `operations`: annual revenue, expenses, required capex, and cash reserves.
- `leases`: area, rent, expiry, reviewed renewal range, business-health signal,
  and AI-related space-demand exposure.
- `scenario_assumptions`: every rate, growth path, renewal case, and threshold
  used by the calculations.
- `reuse_candidates`: operator-selected uses screened across physical,
  regulatory, market, and financial fit.

See [`schemas/trajectory-input.schema.json`](schemas/trajectory-input.schema.json)
for the machine-readable contract.

See [`SOURCE_INTAKE.md`](SOURCE_INTAKE.md) for the owner-document and official
public-source review map that should populate the contract.

## Calculation notes

- Projected revenue scales current revenue by modeled occupied-area retention
  and the visible rent-growth assumption.
- Projected NOI is gross revenue less operating expenses.
- DSCR, LTV, and debt yield use the projected NOI, current balance, and explicit
  debt-service/value paths.
- Refinance proceeds are the lesser of LTV capacity and an interest-only DSCR
  sizing proxy. The output states that lender amortization, fees, covenants,
  reserves, and closing costs are not modeled.
- Reserve movement uses a straight-line/trapezoidal approximation between
  current and horizon cash flow. A later production model should replace this
  with monthly owner-grade cash flow.
- Renewal ranges are operator-reviewed scenario inputs. The low, midpoint, or
  high value is selected according to the named scenario.
- BHI paths use explicit annual point changes supplied for each scenario.

## Guardrails

The engine does not predict default, bankruptcy, foreclosure, layoffs, tenant
intent, or code/entitlement approval. It does not treat acquisition price or
recorded original principal as current loan balance. AI exposure is only a
tenant-diligence flag and never changes renewal assumptions automatically.

Recommendations are deterministic responses to visible thresholds. Reuse
outputs are screens for due diligence, not feasibility conclusions. Before any
investment or lending decision, replace synthetic or inferred values with
current source documents and review the result with qualified legal, lending,
engineering, leasing, and financial professionals.
