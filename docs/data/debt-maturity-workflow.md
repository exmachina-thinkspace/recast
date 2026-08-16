# Debt Maturity Workflow

Purpose: turn the JLL availability list and Recast building universe into a defensible debt-maturity / legal-distress signal that can later feed Recast scoring, ranking, and recommendations.

This workflow was pulled forward from `city-of-seattle-prep/research/real-estate-debt-maturity/README.md` and adapted for Recast.

## Why It Matters

Debt maturity is a separate signal from:

- JLL / CoStar availability;
- assessed-value decline;
- permit silence;
- court litigation;
- physical utilization;
- adaptive-reuse feasibility.

A building with high vacancy and falling value is more actionable if recorded debt, lender activity, or near-term maturity pressure suggests the owner may need to refinance, sell, recapitalize, or hand control to a lender/receiver.

The product goal is not merely:

```text
This building is distressed.
```

The better Recast question is:

```text
Is there evidence that the capital structure is forcing a decision point?
```

## Important Reality Check

Recorded deeds of trust often do not include the actual loan maturity date.

Recorded documents may identify:

- borrower / grantor;
- lender / beneficiary;
- trustee;
- recording date;
- instrument date;
- document type;
- parcel or legal description;
- sometimes loan amount or obligation language.

Maturity may require other sources:

- recorded loan documents, if available;
- loan modification or extension agreements;
- CMBS data, if licensed;
- lender / servicer data, if licensed;
- SEC filings for public owners or REITs;
- press releases or transaction announcements;
- court filings;
- receiver reports;
- owner/lender direct data.

Therefore Recast must keep these fields separate:

```text
known_maturity_date
inferred_maturity_window
unknown_maturity
```

Do not claim a maturity date unless the source explicitly supports it.

## Starting Point: JLL List

Use the JLL availability list as a prioritization queue, not as a final debt source.

For each JLL-matched building:

1. Resolve `building_id`, parcel/APN, address, owner proxy, and legal/property name.
2. Pull existing Recast signals:
   - availability square feet / availability percent;
   - assessed-value peak/current compression;
   - latest sale date and sale price;
   - permit activity;
   - energy rows where available;
   - hero/Tier membership.
3. Use the combined signal to decide which buildings deserve debt-recording research first.

Priority examples:

| Signal pattern | Priority |
| --- | --- |
| High availability + major value compression + 2019-2023 sale/refi | High |
| High availability + known public lender/default reporting | High |
| Moderate availability + value compression + stale permits | Medium |
| Availability only, no value/debt context | Medium/low |
| Healthy/leased building, no distress markers | Low unless future-proofing contrast |

## Data Workflow

The workflow should be ownership/entity-first, not only address-first.

1. Start from a Recast building/parcel.
2. Resolve current owner and related legal entities from assessor/recorder history.
3. Search King County Recorder by parcel, owner/entity names, property names, and legal description when available.
4. Search for recorder document types:
   - statutory warranty deed;
   - bargain and sale deed;
   - quitclaim deed;
   - deed of trust;
   - assignment of deed of trust;
   - modification;
   - extension;
   - substitution of trustee;
   - reconveyance;
   - release;
   - notice of trustee sale;
   - lis pendens;
   - deed in lieu;
   - recorded certified receiver order.
5. Search King County Superior Court / KC Script by owner entities, property names, lender names, and receiver/foreclosure terms.
6. Search Washington Courts case search for statewide spillover cases.
7. Search legal notices for receiver notices, claims bar dates, sheriff sales, and trustee sales.
8. Join hits back to the parcel/building spine.
9. Store source, date, document/case number, court/recorder system, parties, extraction method, and confidence.

## Suggested Recast Tables

Do not create these until the first debt-data source is approved. This is the target shape.

### `recast.debt_instrument`

```text
debt_instrument_id
building_id
source_parcel_id
recording_number
document_type
recording_date
instrument_date
grantor_borrower
grantee_beneficiary_lender
trustee
loan_amount
legal_description_present
source_document_reference
source_url_or_path
checksum
extraction_method
evidence_label
confidence
notes
```

### `recast.debt_event`

```text
debt_event_id
debt_instrument_id
building_id
source_parcel_id
event_type
event_date
event_source
event_description
lender_or_beneficiary
trustee
amount
evidence_label
confidence
```

Event types:

```text
purchase
deed_of_trust_recorded
refinance
assignment
modification
extension
substitution_of_trustee
reconveyance
release
notice_of_trustee_sale
foreclosure
receiver_requested
receiver_appointed
```

### `recast.maturity_estimate`

```text
maturity_estimate_id
debt_instrument_id
building_id
source_parcel_id
known_maturity_date
inferred_maturity_start
inferred_maturity_end
maturity_basis
maturity_source
evidence_label
confidence
needs_manual_review
notes
```

Maturity basis:

```text
explicit_document_date
loan_modification
extension_agreement
CMBS_source
court_filing
press_release
inferred_5_year_term_from_recording
inferred_7_year_term_from_recording
inferred_10_year_term_from_recording
unknown
```

### `recast.debt_maturity_signal`

```text
building_id
source_parcel_id
owner_entity
latest_sale_date
latest_sale_price
latest_debt_event_date
latest_debt_event_type
latest_lender
known_maturity_date
inferred_maturity_window
assessed_value_peak
assessed_value_current
value_decline_amount
value_decline_percent
available_sf
availability_pct
legal_distress_status
debt_maturity_state
evidence_tier
next_verification_step
source_load_run_id
```

## Recast Signal Labels

Use categorical signal labels before a numeric score.

```text
MATURITY_KNOWN_NEAR_TERM
MATURITY_INFERRED_NEAR_TERM
REFI_OR_EXTENSION_FOUND
POSSIBLY_PAID_OFF
LEGAL_DISTRESS_ACTIVE
FORECLOSURE_OR_LENDER_ACTION
RECEIVERSHIP_REQUESTED
RECEIVER_APPOINTED
INSUFFICIENT_DEBT_EVIDENCE
NO_SIGNAL_FOUND
```

Receivership should be treated as `KNOWN` only when supported by a court docket/order or a recorded real-property document.

Before a petition/application exists, use:

```text
PRE_RECEIVERSHIP_RISK_SIGNAL
LEGAL_OR_FINANCIAL_DISTRESS_SIGNAL
```

Do not say "going into receivership" without a filed petition/application or stronger source.

## How It Should Factor Into Recast

Debt maturity should affect Recast attention and recommendation confidence, not act as a standalone verdict.

Good Recast interpretation:

```text
High availability + value compression + near-term maturity/lender action
= stronger current-state weakness and more urgent decision point
```

Bad Recast interpretation:

```text
2019 purchase date = distressed
```

Recommended influence:

| Recast layer | Debt maturity contribution |
| --- | --- |
| As-Is Intelligence | Capital-structure pressure, ownership/control stress, legal distress |
| Trajectory | Upcoming maturity/refi window, lender action, extensions/refis, payoff/reconveyance |
| Recast Opportunity | Urgency and actionability of reposition/convert/recast path |
| Capital Stack | Need for recapitalization, public financing, C-PACER, grants, tax deferral, affordable-housing capital |
| Judge/demo evidence | Explain why this building is not merely vacant, but may be approaching a forced decision |

When a numeric Recast score exists later, debt maturity should be one bounded component with evidence tiers and confidence. For Demo 1, use labels and evidence cards.

## First Recast Pass

For the JLL-matched list:

1. Select the JLL buildings already matched to Recast building IDs.
2. Rank by availability percent, available square feet, value compression, latest sale date, and permit silence.
3. For the top 10-20, run the recorder/court/legal-notice workflow.
4. Produce `debt_maturity_signal` rows only when source evidence has been reviewed.
5. Leave all uncertain results as `INSUFFICIENT_DEBT_EVIDENCE` with `next_verification_step`.

## Sources To Use First

Public / lower-friction:

- King County Recorder;
- King County Superior Court / KC Script;
- Washington Courts case search;
- legal notices;
- SEC filings / public-company disclosures;
- press releases and transaction reporting.

Licensed / commercial:

- First American Data & Analytics / DataTree;
- ATTOM;
- ICE Mortgage Technology property data;
- Cotality / CoreLogic;
- CMBS/special-servicer datasets where licensed.

## Demo Posture

For Demo 1, only show debt-maturity facts if they have a source.

Acceptable demo phrasing:

```text
Debt maturity: insufficient evidence
Debt signal: deed of trust recorded; maturity unknown
Debt signal: lender/default reporting found; source review required
Legal distress: receiver appointed, verified by court order
```

Do not show:

```text
Loan maturity: 2027
```

unless a document or licensed source actually says that.
