---
title: Citywide 3D View - Plan
type: feat
date: 2026-08-15
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Citywide 3D View - Plan

## Goal Capsule

- **Objective.** Give Demo 1 its city-scale moment: downtown Seattle rendered on Google Photorealistic 3D Tiles, every candidate office building painted by a signal, one click opens the building's evidence-tiered record, and the Building Health Index (BHI) can drive the colours as soon as scores exist.
- **Authority.** `docs/demo1/06-3d-digital-twin.md` (visual requirements and guardrails) > `docs/demo1/01-demo-thesis.md` > this plan. Product principle from `README.md`: every claim is KNOWN / OBSERVED / INFERRED / UNKNOWN / INSUFFICIENT_EVIDENCE.
- **Stop conditions.** Stop and ask before: adding a build step or framework; committing any credential other than the hackathon Google key already in the file; changing the evidence-tier vocabulary; publishing scores for buildings that have no record.
- **Execution profile.** Single self-contained HTML file (`apps/city-view-3d/seattle-office-vitals-3d.html`), CesiumJS from CDN, data inline; verify by opening the file in a browser. Units U1–U6 shipped in commit `0898973`; U7–U10 are open.
- **Tail ownership.** Whoever runs a unit runs `/ce-compound` on anything non-obvious learned and updates `apps/city-view-3d/CLAUDE.md` if the data model or integration points change.

---

## Product Contract

### Summary

A judge should look at the screen and, within seconds, see which buildings deserve attention and click one to see why. The view paints buildings onto Google's photorealistic mesh (not abstract extrusions), keeps everything else gray, and opens a record card whose every number carries an evidence tier. Colour encodes share leased today and switches to BHI when the scorer's records are attached.

### Problem Frame

Records tell you what is on file; the demo has to show what a building is *doing*. A flat map of dots does not read as "the city"; a full-colour photogrammetry mesh hides the data. The view has to make the data the loudest thing on screen while keeping the recognisable city underneath, and it must not invent numbers where evidence is missing.

### Requirements

**View**
- R1. Downtown/SLU renders on Google Photorealistic 3D Tiles with the imagery desaturated and dimmed so painted buildings dominate; a control restores full colour.
- R2. Each building in the list is painted on the mesh within its footprint (roof and facades), not represented by a marker; buildings without a footprint fall back to a pin.
- R3. Paint colour encodes share leased by default and BHI when records exist; buildings without a BHI record are gray (INSUFFICIENT_EVIDENCE) in BHI mode.
- R4. Chips filter the painted set (≤25% leased, ≤10%, no energy value); a reset returns the camera to the city view.

**Interaction**
- R5. Clicking a painted building or pin opens its record card; clicking plain city or Esc closes it. Hover brightens the building under the cursor.
- R6. The selected building is unmistakable: distinct saturated colour, other paint stepped back, dark ring at its base, label emphasised.
- R7. The card offers Street View, Google Maps and Fly-to.

**Record card**
- R8. The card opens with the BHI block: score /100 with evidence coverage when a record exists; otherwise an explicit INSUFFICIENT_EVIDENCE statement — never a placeholder number.
- R9. Five vitals (Use / Utilization, Clean & Safety, Economic Sustainability, Community Engagement, Productivity & Upkeep) each show a tier tag and their evidence; with a record they also show score · weight · coverage and one line per input.
- R10. One section per data source (availability, Seattle Energy Benchmarking, OSM footprint), each with caveat flags (missing record, neighbour match, no footprint).

**Data and integration**
- R11. Building list, footprints and BHI records live inline in the HTML and are replaced with a tool, not hand-edited; the page works when opened from disk.
- R12. BHI records are consumed in the exact JSON shape written by `spark-3d-pipeline/src/twin/build_vitals.py` (T0–T3 tiers), keyed by building number.
- R13. Footprints are © OpenStreetMap contributors and attributed on screen; Google and Cesium credits stay visible.

### Scope Boundaries

- In scope: the citywide layer, record cards, colour modes, data tooling, agent-facing documentation.
- Out of scope: interior digital twin, camera path automation for the pitch, computing any score inside the page, a building list panel (removed by design — the map is the index).

### Success Criteria

- A first-time viewer identifies a highlighted building and opens its card without instruction.
- With BHI records for the Tier 0/1 buildings attached, colour mode switches and the legend reads correctly with no code change.
- No console errors on load in Chrome and Safari; the page opens from `file://`.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **Highlight by classification, not by extrusion.** Google's tiles are one fused mesh with no per-building objects; a footprint extruded into a classification volume tints the mesh inside it, so the real building lights up. Rationale and gotchas: `docs/solutions/architecture-patterns/highlight-buildings-on-google-photorealistic-3d-tiles.md`.
- KTD2. **`GroundPrimitive`, not `ClassificationPrimitive`.** Per-building colours in one batched primitive are only allowed through `GroundPrimitive` (`docs/solutions/runtime-errors/classification-primitive-per-instance-colors-require-ground-primitive.md`).
- KTD3. **Hit-test by mesh position, not pick id.** Cesium's shadow-volume pick pass does not cull per instance; the page resolves clicks with `scene.pickPosition` plus point-in-polygon against the same footprints that drive the paint (`docs/solutions/ui-bugs/cesium-shadow-volume-pick-returns-wrong-building.md`).
- KTD4. **OSM footprints, buffered 2 m.** Point-in-polygon on the listed coordinate, nearest polygon ≤15 m, hand overrides; buffer catches photogrammetry bulge and small overhangs (`docs/solutions/best-practices/osm-footprint-matching-for-geocoded-addresses.md`).
- KTD5. **Desaturate and dim in a `CustomShader`** on the tileset rather than pre-processing tiles; toggles instantly.
- KTD6. **Data inline, single file, no fetch.** `fetch()` of local files fails on `file://`; a small tool swaps the inline constants (`docs/solutions/developer-experience/single-file-cesium-page-with-inline-data.md`).
- KTD7. **Evidence vocabulary is the repo's.** Scorer tiers map T1→OBSERVED, T2→KNOWN, T3→INFERRED, T0→UNKNOWN; no record → INSUFFICIENT_EVIDENCE (`docs/solutions/conventions/building-record-card-evidence-tiers.md`).
- KTD8. **Hackathon key baked in.** Accepted trade-off for the event; key is deleted afterwards (`docs/solutions/best-practices/google-map-tiles-api-key-for-local-html-demos.md`).

### High-Level Technical Design

`B` (buildings) → per building: label entity (+ pin if no footprint) and one instance in a batched `GroundPrimitive` classification volume from `F` (footprints) → colour from `colorBy` (`leasedCol` / `bhiCol` over `BV`) → click: `pickBuilding` → `select` → paint sync, base ring, label pill, `renderDetail` (card from `bhi(b)` + source sections). Tileset gets `grayShader` (`u_gray`, `u_dim`).

### Assumptions

- The availability list (124 buildings) is a reasonable stand-in for the Recast attention set until `recast.building` (69) is mapped in.
- Google Map Tiles quota is fine for demo-day traffic on one key.

---

## Implementation Units

### U1. Paint buildings on the mesh
- **Goal:** replace roof pins with painted footprints (R2).
- **Requirements:** R2, R13.
- **Files:** `apps/city-view-3d/seattle-office-vitals-3d.html` (`buildFootprints`, `fpColor`, `syncFootprints`), `apps/city-view-3d/tools/build_footprints.py`, `apps/city-view-3d/data/footprints.json`.
- **Approach:** OSM footprints per building; batched `GroundPrimitive` with `classificationType: CESIUM_3D_TILE`; per-instance colour/show attributes.
- **Test scenarios:** roof and facades of a tower take the colour; neighbours unaffected; buildings without footprint keep pin.
- **Verification:** open file, inspect Fourth & Battery and Fourth & Vine at close range.

### U2. Gray, dimmed city
- **Goal:** imagery recedes so paint reads (R1).
- **Files:** `seattle-office-vitals-3d.html` (`grayShader`, "Gray map" chip).
- **Approach:** `CustomShader` mixing tile colour toward luminance and toward the page background.
- **Verification:** toggle chip; paint colours unchanged; imagery at ~60% over background.

### U3. Reliable selection
- **Goal:** click/hover always resolve to the painted building (R5).
- **Files:** `seattle-office-vitals-3d.html` (`footprintAt`, `pickBuilding`).
- **Approach:** `scene.pick` for entities; `scene.pickPosition` + point-in-polygon for footprints; hover throttled.
- **Test scenarios:** probing several pixels of one building returns that building; plain city returns null.
- **Verification:** `pickBuilding(new Cesium.Cartesian2(x,y))` at known pixels.

### U4. Record card
- **Goal:** click opens the evidence-tiered card on the right (R7–R10).
- **Files:** `seattle-office-vitals-3d.html` (`renderDetail`, `bhi`, `TIER`, `.detail` styles).
- **Approach:** BHI block → vitals → actions → one `src` section per data source; light card palette; close on ×/Esc/plain click.
- **Verification:** card content for a building with and without a benchmarking record; flags appear where expected.

### U5. Selection emphasis
- **Goal:** the selected building is unmistakable (R6).
- **Files:** `seattle-office-vitals-3d.html` (`fpColor`, `syncRing`, `select`).
- **Approach:** cyan paint at 0.95, others at 0.45, polygon-with-hole ring volume at the base, label pill.
- **Verification:** select, screenshot, deselect restores all state (`ringPrim === null`, colours back).

### U6. BHI data slot and colour mode
- **Goal:** consume scorer output with no code change (R3, R8, R9, R12).
- **Files:** `seattle-office-vitals-3d.html` (`BV`, `bhiCol`, `renderLegend`, "Color: BHI" chip), `apps/city-view-3d/tools/embed_json.py`, `apps/city-view-3d/data/build_vitals.example.json`.
- **Approach:** `BV` keyed by building #, adapter in `bhi(b)`, chip appears when `BV` non-empty, gray for no record.
- **Verification:** inject the example record at runtime; card shows 58/100 with tiers; legend switches.

### U7. Attach real BHI records (open)
- **Goal:** Tier 0/1 buildings colour by BHI on demo day (R3, R12).
- **Requirements:** R3, R8, R9, R12.
- **Files:** `apps/city-view-3d/data/build_vitals_all.json` (new), `seattle-office-vitals-3d.html` (`BV` via tool).
- **Approach:** run the scorer per building, emit `{ "<i>": build_vitals.json }`, join on address/parcel where the scorer lacks `i`, `embed_json.py --var BV --merge`.
- **Test scenarios:** unscored buildings gray in BHI mode; a scored building shows its inputs and tiers; weights visible.
- **Verification:** open file, toggle Color: BHI, open two cards (scored, unscored).

### U8. Map onto the Recast building spine (open)
- **Goal:** the page's building numbers resolve to `recast.building` rows so future data joins are direct.
- **Files:** `seattle-office-vitals-3d.html` (`B` via tool), `apps/city-view-3d/CLAUDE.md`.
- **Approach:** add `bid`/`parcel` fields to `B` from `source_outerspaces.availability_signal` / `seattle_building_energy_benchmarking_subset` by normalised address; keep `i` as the page key.
- **Verification:** every Tier 0 building carries a `bid`; card unchanged.

### U9. Add value trajectory and permit activity to the card (open)
- **Goal:** two more record sections from `recast.building_value_trajectory` and `recast.building_permit_activity` (R10).
- **Files:** `seattle-office-vitals-3d.html` (`renderDetail`), `B` fields via tool.
- **Approach:** new `src` heading + `dl(rows)` each, with caveat flags; if either feeds a vital, route it through the scorer so it arrives with a tier.
- **Verification:** sections render for a building with data and show a flag without.

### U10. Hero-building choreography (open)
- **Goal:** the pitch's zoom-in / zoom-out moment on the chosen hero (`docs/demo1/06-3d-digital-twin.md`).
- **Files:** `seattle-office-vitals-3d.html` (`resetView`, `flyTo`, optional URL parameter to preselect).
- **Approach:** `?b=<i>` preselects and flies; camera constants tuned for the hero; keep manual controls.
- **Verification:** load with the parameter; card open, building selected, camera framed.

---

## Verification Contract

- Open `apps/city-view-3d/seattle-office-vitals-3d.html` from disk in Chrome and Safari; no console errors; no key gate.
- Click a painted building → card; click plain city → closes; Esc closes; chips filter; Gray map toggles.
- `python3 apps/city-view-3d/tools/build_footprints.py` reproduces the embedded `F` unchanged (byte-identical after rebuild).
- `python3 apps/city-view-3d/tools/embed_json.py --var BV data/build_vitals.example.json` then open → Color: BHI chip present; card for building 51 shows 58/100 with tiers; restore with an empty object.
- A backgrounded tab renders black until foregrounded — verify in a visible tab.

## Definition of Done

- All requirements above demonstrable in the browser; unit-level verification steps pass.
- No dead-end code left in the HTML; data constants replaced only via the tool.
- `apps/city-view-3d/CLAUDE.md` reflects any change to data model or integration points.
- Learnings from any unit that fought the platform are captured under `docs/solutions/` (`/ce-compound`).
