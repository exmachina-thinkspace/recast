---
title: Building record cards show evidence tiers and never a fake score
date: 2026-08-15
category: conventions
module: city-view-3d
problem_type: convention
component: frontend
severity: high
applies_when:
  - "Rendering a Building Health Index, vital, or any per-building number in the Recast UI"
  - "Mapping build_vitals.py tiers (T0-T3) onto the Recast product vocabulary"
tags: [bhi, evidence-tiers, insufficient-evidence, build-vitals, record-card, vocabulary]
---

# Building record cards show evidence tiers and never a fake score

## Context

The Build Vitals brief and `README.md` require every claim to be tagged KNOWN / OBSERVED / INFERRED / UNKNOWN /
INSUFFICIENT_EVIDENCE, with weights visible and no invented numbers. The scorer (`spark-3d-pipeline/src/twin/build_vitals.py`)
uses tiers T1 (direct observation), T2 (official record), T3 (proxy/inferred), T0 (no evidence).

## Guidance

- Card order: BHI block → five vitals → actions → one section per data source. The BHI block shows `score /100`,
  evidence coverage and the scoring timestamp when a record exists; otherwise it says
  `INSUFFICIENT_EVIDENCE — N of 5 vitals have evidence on file; score not computed` and shows a dash, never 0 or a guess.
- Tier mapping: T1 → OBSERVED, T2 → KNOWN, T3 → INFERRED, T0 → UNKNOWN. A vital's tag is the best tier among its
  inputs; each input renders as `[T1] source — note` so the reader can see what the score rests on.
- Weights stay visible on the vital row (`score · w 0.25 · 60% cov`).
- Colour by BHI only for buildings with a record; everything else is gray = insufficient evidence, and the legend says so.
- The page never computes a score; it consumes the scorer's JSON as written (`BV` slot). Determinism lives in the
  scorer; the UI explains.

## Why This Matters

A low score affects a real owner's asset and a judge will probe the first number that looks invented. Honest gaps are
part of the pitch ("gray does not mean healthy — it means Recast does not yet have enough evidence").

## When to Apply

- Every Recast surface that shows a building-level score, vital, or trend.

## Examples

`apps/city-view-3d/seattle-office-vitals-3d.html`: `TIER`, `bhi(b)`, `renderDetail()`; example record in
`apps/city-view-3d/data/build_vitals.example.json`.

## Related

- `docs/demo1/06-3d-digital-twin.md` (guardrails and demo line)
