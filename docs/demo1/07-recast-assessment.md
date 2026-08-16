# Recast Assessment

## Goal

Turn verified records signals, physical evidence, and incentive/program facts into three bounded answers:

```text
How is this building doing today, and where is it heading?
What could this building become?
What could make that transformation economically possible?
```

## Evidence Model

Keep the distinction clear:

```text
Records Signal
Physical Evidence
Debt Maturity / Legal Distress Evidence
Capital Stack / Incentive Evidence
Recast Assessment
```

A video clip can support a physical observation. It cannot correct whole-building vacancy or utilization by itself.

## Evidence Labels

Every factor must be labeled:

```text
KNOWN
OBSERVED
INFERRED
UNKNOWN
INSUFFICIENT_EVIDENCE
```

## Assessment Shape

```json
{
  "building_id": "string",
  "as_is": {
    "summary": "string",
    "state": "healthy|vulnerable|distressed|severely_distressed|insufficient_evidence",
    "trajectory": "stable|improving|deteriorating|unclear",
    "signals": [
      {
        "name": "value_decline",
        "evidence_label": "KNOWN",
        "source_ref": "string",
        "claim": "string"
      }
    ]
  },
  "debt_maturity": {
    "state": "maturity_known_near_term|maturity_inferred_near_term|refi_or_extension_found|possibly_paid_off|legal_distress_active|foreclosure_or_lender_action|receivership_requested|receiver_appointed|insufficient_debt_evidence|no_signal_found",
    "evidence_label": "KNOWN|INFERRED|UNKNOWN|INSUFFICIENT_EVIDENCE",
    "known_maturity_date": "date|null",
    "inferred_maturity_window": "string|null",
    "latest_debt_event_type": "string|null",
    "latest_debt_event_date": "date|null",
    "latest_lender": "string|null",
    "source_ref": "string|null",
    "next_verification_step": "string"
  },
  "physical_evidence": [
    {
      "question": "Show me large open floorplates.",
      "evidence_label": "OBSERVED",
      "source_ref": "vss://clip/...",
      "timestamp_start": "string",
      "timestamp_end": "string",
      "claim": "string",
      "limitations": "string"
    }
  ],
  "recast_options": [
    {
      "use": "residential",
      "outcome": "KEEP|IMPROVE|RETROFIT|REPOSITION|CONVERT|RECAST",
      "rank": 1,
      "why": ["string"],
      "risks": ["string"],
      "unknowns": ["string"],
      "fit": {
        "physical": "known|observed|inferred|unknown|insufficient_evidence",
        "regulatory": "known|inferred|unknown|insufficient_evidence",
        "market": "known|inferred|unknown|insufficient_evidence",
        "financial": "inferred|unknown|insufficient_evidence",
        "incentive": "potential|verified|unknown|insufficient_evidence"
      },
      "capital_stack_support": [
        {
          "program": "string",
          "agency": "string",
          "support_type": "grant|credit|loan|tax_deferral|incentive|technical_assistance",
          "eligibility_status": "verified_eligible|potentially_relevant|not_eligible|unknown",
          "source_ref": "string",
          "notes": "string"
        }
      ],
      "confidence": "low|medium|high"
    }
  ]
}
```

## Candidate Reuse Classes

Rank only the classes supported by available data:

- multifamily / residential conversion;
- supportive housing / shelter;
- education or civic use;
- lab / light industrial;
- data center;
- hospitality;
- renewed commercial use;
- hold / sale / no-action;
- insufficient evidence.

## Fit Questions

For each candidate future, Recast should eventually answer:

| Fit | Question |
| --- | --- |
| Physical Fit | Can this building plausibly support the use? |
| Regulatory Fit | Does zoning/code/regulatory context make it plausible? |
| Market Fit | Is there evidence of demand or need? |
| Financial Fit | Could the economics plausibly work? |
| Incentive Fit | What grants, credits, subsidies, incentives, or public financing could improve the economics? |

## Debt Maturity Signal

Debt maturity is a Recast signal input, not a standalone verdict.

Use the workflow in [debt-maturity-workflow.md](../data/debt-maturity-workflow.md) to turn JLL-matched buildings into evidence-backed debt and legal-distress labels.

For Demo 1 and early scoring, use categorical states before a numeric subscore:

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

Debt maturity should influence:

- As-Is weakness;
- trajectory risk;
- urgency / actionability;
- confidence in a Recast opportunity;
- capital-stack needs.

It should not turn a building into a distressed asset by itself. A 2019-2023 sale/refi date is only a queueing signal until the recorder, court, lender, or licensed-source evidence is reviewed.

## Recommendation Rules

- Do not make final feasibility claims.
- Do not claim code compliance.
- Do not claim conversion cost certainty.
- Do not recommend a use without showing the top factors and risks.
- Do not claim verified incentive eligibility unless the program requirements support it.
- Distinguish `potentially_relevant` from `verified_eligible`.
- Do not claim a debt maturity date unless a recorded document, court filing, licensed data source, or other source explicitly supports it.
- Do not say "receiver appointed" unless supported by a court order/docket or recorded real-property document.
- Always include what evidence would be needed next.

## Judge Payoff

The recommendation should feel like:

```text
Recast is not just saying this building is distressed.
It understands the building lifecycle: current state, future risk, better use, and what could help pay for the transition.
```
