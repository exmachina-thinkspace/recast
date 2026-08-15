---
title: Keep a Cesium demo as one HTML file with inline data and a tool to swap it
date: 2026-08-15
category: developer-experience
module: city-view-3d
problem_type: developer_experience
component: tooling
severity: medium
applies_when:
  - "A demo page must open by double-click (file://) on any teammate's machine with no server or build step"
  - "Teammates or agents need to inject datasets (buildings, footprints, scores) without hand-editing 100 KB lines"
tags: [single-file, file-url, fetch, inline-json, tooling, cesium, embed]
---

# Keep a Cesium demo as one HTML file with inline data and a tool to swap it

## Context

Judges, teammates and agents open the citywide view from disk. `fetch()` of sibling JSON fails on `file://` in Chrome
(CORS), so data cannot be loaded at runtime; but nobody should hand-edit a 100 KB `const B = [...]` line either.

## Guidance

- Keep every dataset as a named constant on its own line: `const B = [...];`, `const F = {...};`, `const BV = {...};`
  (JSON-serialisable, minified). Load libraries from CDN; no bundler.
- Ship a tiny embed tool that swaps one constant from a JSON file by regex on `const NAME = (...);` — with `--merge`
  for keyed objects, `--show` to inspect, and dropping `_comment` keys. All data changes go through it.
- Ship the data pipeline that produced the constant (footprint builder) so the constant is reproducible; make the
  builder idempotent and prove it (rebuild → byte-identical).
- Document the data model and integration points in a `CLAUDE.md` in the folder so the next agent starts informed.
- When testing from an agent, serve the folder over `http://localhost` (localStorage then persists) — a browser pane
  that loads `file://` as a `data:` URL cannot keep localStorage, and a backgrounded tab renders black
  (`docs/solutions/performance-issues/cesium-tiles-black-when-tab-is-hidden.md`).

## Why This Matters

The whole team, plus their coding agents, could integrate scores in one command instead of one afternoon, and the
page never depends on where it is opened from.

## When to Apply

- Hackathon/demo pages; anything a non-developer must open from a shared folder.

## Examples

`apps/city-view-3d/tools/embed_json.py --var BV --merge data/build_vitals_all.json`;
`apps/city-view-3d/tools/build_footprints.py`.

## Related

- `docs/solutions/best-practices/google-map-tiles-api-key-for-local-html-demos.md`
