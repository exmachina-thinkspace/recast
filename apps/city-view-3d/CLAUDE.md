# city-view-3d — context for Claude Code

You are working in the **citywide 3D view** of the Recast / Build Vitals hackathon demo (NVIDIA Spark hackathon,
Seattle, Aug 2026). It is one self-contained HTML file that paints office buildings onto Google's Photorealistic
3D Tiles of downtown Seattle and opens a record card when a building is clicked. Your job is usually one of:

1. attach **Building Health Index (BHI)** results so buildings colour by health and the card shows the scorecard;
2. add a **data source** to the card (permits, assessor value history, complaints, sensor tier, …);
3. add or replace **buildings** in the list;
4. tune camera / colours / copy for the demo.

Everything you need is in this folder. Read this file fully before editing `seattle-office-vitals-3d.html`.

## Files

| Path | What it is |
| --- | --- |
| `seattle-office-vitals-3d.html` | The whole app: CSS, HTML, data constants, JS. ~110 KB, no build step, no bundler. Open it from disk. |
| `tools/embed_json.py` | Replace (or `--merge`) one of the inline data constants `B` / `F` / `BV` from a JSON file. **Use this instead of hand-editing the data lines.** |
| `tools/build_footprints.py` | Regenerate the OSM footprints (`F`) for whatever is in `B`: Overpass fetch → match → buffer → embed. Standard library only. |
| `data/footprints.json` | The matched footprints with metadata (`osm` id, `nm` name, `lv` levels, `h` height, `how` matched). Reference / audit; the HTML embeds a leaner, 2 m-buffered copy. |
| `data/build_vitals.example.json` | The exact `BV` record shape (one building). Copy its structure. |
| `data/osm_raw.json` | Overpass cache (git-ignored, ~12 MB). `build_footprints.py --refetch` recreates it. |

## Runtime facts (don't fight these)

- **CesiumJS 1.128 from the Cesium CDN**, `viewer` with `globe:false`, `requestRenderMode:true`. After changing anything visual call `viewer.scene.requestRender()`.
- **Google Map Tiles API** (Photorealistic 3D Tiles) via `Cesium3DTileset.fromUrl('https://tile.googleapis.com/v1/3dtiles/root.json?key=…')`. The key is hard-coded in `DEFAULT_KEY` (line ~208) on purpose for the hackathon; it is being deleted after the event. `?key=…` in the URL or a key saved in localStorage overrides it. Keep the Google credit visible (`showCreditsOnScreen:true`).
- The tiles are **one fused mesh** — there are no per-building objects to select or recolour. Buildings are "highlighted" by **classification**: each footprint becomes a `GroundPrimitive` volume with `classificationType: CESIUM_3D_TILE`; the tile mesh inside the volume is tinted. See `buildFootprints()` / `fpColor()` / `syncFootprints()`.
- A `CustomShader` (`grayShader`) desaturates and dims the tiles (`u_gray`, `u_dim=0.6`). The "Gray map" chip toggles `u_gray`.
- **Hit-testing does not use the classification pick id** — Cesium's shadow-volume pick pass doesn't cull per instance, so ids bleed between neighbours. `pickBuilding()` picks pins/labels via `scene.pick`, then falls back to `footprintAt()`: `scene.pickPosition` (mesh point under the cursor) → point-in-polygon against `F`. Keep it that way.
- Footprints are © OpenStreetMap contributors (ODbL); the attribution line in the legend must stay.
- The page must keep working when opened as `file://` — so **no `fetch()` of local files**; data is inline (that's what `tools/embed_json.py` is for). Serving over http is fine too.

## Data model

### `B` — the building list (line ~156, JSON array, 124 records)
One object per building. Field names are short because the array is inline:

| field | meaning | source |
| --- | --- | --- |
| `i` | building # — the **join key** everywhere (`F`, `BV`, pick ids `'f'+i`, entity ids `'b'+i`) | — |
| `a`, `n` | address, building name (may be `""`) | availability report |
| `la`, `lo` | latitude, longitude of the listed address (WGS84) | geocoded |
| `r`, `l`, `f`, `y` | rentable area (sf), % leased (`null` = not disclosed), stories, year built | availability report, Aug 2026 |
| `nb` | neighborhood (`"DOWNTOWN"`, `"LAKE UNION"`, …) | benchmarking |
| `e`, `s`, `g`, `u` | site EUI kBtu/sf, ENERGY STAR score, gross floor area sf, primary use | Seattle Building Energy Benchmarking 2024 |
| `m`, `ma`, `mn`, `my` | benchmarking match quality (`exact/high/near/none`) and the matched record's address/name/year | join audit |

To change the list: write a JSON array with these fields and run `python3 tools/embed_json.py --var B new.json`, then `python3 tools/build_footprints.py` to refresh footprints. New fields are fine (they ride along on `b`), then read them in `renderDetail()` / `bhi()`.

### `F` — footprints (line ~160, JSON object keyed by `i` as a string)
`{ "51": { "p": [[lon,lat],…], "nm": "Fourth & Battery Building", "lv": 12, "h": 45.4, "osm": "way/…" }, … }`
`p` is the outer ring, already buffered 2 m outward. Buildings missing from `F` (8 today: unbuilt/proposed sites and one unmatched) show as a pin at `la/lo` instead. Regenerate with `tools/build_footprints.py`; hand-fix a bad match by adding to `OVERRIDES` in that script.

### `BV` — Build Vitals records (line ~165, JSON object keyed by `i`, **empty today**)
This is the BHI hookup. Each value is one `build_vitals.json` exactly as `report()` in
`spark-3d-pipeline/src/twin/build_vitals.py` writes it:

```json
{ "bhi": 58.4, "evidence_coverage": 0.62, "generated": "2026-08-15T14:20:00",
  "vitals": { "use_utilization": { "label": "Use / Utilization", "score": 71.2, "weight": 0.25, "evidence_coverage": 0.6,
              "inputs": [ { "source": "camera occupancy", "value": 71.2, "weight": 0.6, "tier": "T1", "note": "…" } ] },
              "clean_safety": {…}, "economic": {…}, "community": {…}, "productivity_upkeep": {…} } }
```
Tiers map onto the Recast product vocabulary: `T1` direct observation → OBSERVED, `T2` official record → KNOWN,
`T3` proxy/inferred → INFERRED, `T0` no evidence → UNKNOWN (the vital tag is the best tier among its inputs); a building
with no record at all shows INSUFFICIENT_EVIDENCE. Full example: `data/build_vitals.example.json`.

**To integrate the BHI:** produce `{ "<i>": <build_vitals.json>, … }` for the buildings you scored and run
`python3 tools/embed_json.py --var BV --merge data/build_vitals_all.json`. Nothing else is required:

- `bhi(b)` (line ~544) reads `BV[b.i]` and normalises it for the card; buildings without a record keep the honest
  "INSUFFICIENT_EVIDENCE — N of 5 vitals have evidence on file; score not computed" block.
- The **Color: BHI** chip appears automatically when `BV` is non-empty; it colours buildings on the health ramp
  `--h0..--h4` (0 = critical → 100 = healthy) and **gray for insufficient evidence** — never invent a score for a
  building without a record. Legend text switches with it (`renderLegend()`).
- Card shows BHI /100, evidence coverage, scored timestamp, and per vital: score · weight · coverage, tier tag,
  one line per input `[T1] source — note`.

Join on address if your scorer doesn't know `i`: `B` has `a` (address) and `la/lo`; the pipeline's `BUILDING.address`
can be matched by house number + street. Do the join in your script, emit keyed by `i`.

**Recast Postgres (GB100, see `docs/data/`)** is the team's building spine: `recast.building` (69 local buildings today),
`recast.building_availability`, `recast.building_energy_signal`, `recast.building_permit_activity`,
`recast.building_value_trajectory`, and the `source_outerspaces.*` tables (`availability_signal.building_id/address`,
`seattle_building_energy_benchmarking_subset.ose_building_id/tax_parcel_identification_number/address`). This page's
`B` predates that spine and is keyed by its own `i`; when you map a building to a `recast.building` row, add the id to
the `B` record (e.g. `"bid": "…"`, `"parcel": "…"`) via `embed_json.py --var B` so future joins are direct.

## Rules from the Build Vitals brief (enforce in anything you add)

- Every input carries an evidence tier; **INSUFFICIENT EVIDENCE shows gray, never a fake number.**
- Weights are visible (`w 0.25` on each vital row) — keep them visible if you restyle.
- Deterministic code computes the score; the AI explains. Don't compute a BHI inside this page — consume `BV`.
- Don't claim precision the data can't support (see `demo1/06-3d-digital-twin.md` guardrails).

## Where things are in the HTML (line numbers approximate)

| Anchor | Purpose |
| --- | --- |
| `:root{…}` CSS vars | palette. `--v0..--v4` leased ramp, `--h0..--h4` health ramp, `--vnull` gray, `--map-sel` cyan for the selected building. Cards are light; the map is dark. |
| `<div class="panel">` / `.panel.right` | left: title, filter chips, legend. right: the building card `#detail` (hidden until a click). |
| `const B / F / BV` ~156–165 | data. Use `tools/embed_json.py`. |
| `colorBy`, `vcol()`, `renderLegend()` ~171 | how a building gets its colour + legend copy. |
| `passes(b)` ~190 | filter chips (`all / vacant / empty / noenergy`) — add a chip in HTML + a case here. |
| `DEFAULT_KEY`, `start()` ~208 | key + tileset load. Gate UI only appears if the key fails. |
| `grayShader` ~248 | tile desaturation/dim. |
| `buildViewer()` ~282 | Cesium viewer, one entity per building (label always; pin only when no footprint), click/hover handlers. |
| `footprintAt()` / `pickBuilding()` ~366 | hit-testing (see runtime facts). |
| `fpColor()` / `syncRing()` / `buildFootprints()` / `syncFootprints()` ~393–480 | paint colours, selection ring, the classification primitive and its per-instance colour/show sync. |
| `resetView()` / `flyTo()` ~481 | camera choreography (`resetView` = over Elliott Bay looking NE; `flyTo` = 350 m SW of the building). |
| `select()` / `deselect()` ~514 | selection state → paint, ring, label pill, card. |
| `TIER`, `bhi()` ~542 | BV adapter (see above). |
| `renderDetail()` ~579 | the card. One `src` heading + `dl(rows)` per data source; add a source as another pair. `flag` divs carry caveats. |

## Adding a data source to the card (recipe)

1. Put the values on the building objects (new fields in `B` via `embed_json.py --var B`, or a new keyed map like `BV`).
2. In `renderDetail()`, add `'<div class="src">King County assessor · 2015–2026</div>' + dl([['Assessed value', …], …])`
   next to the existing sections, and a `flag` div for caveats (missing record, neighbour match, stale year).
3. If it feeds a vital, don't wire it into the card only — feed it to the scorer so it arrives through `BV` with a tier.
4. Open the file, click a building, check the card; hover/click must still resolve (`pickBuilding`), no console errors.

## Verifying a change

Open `seattle-office-vitals-3d.html` in Chrome/Safari (double-click). Expect: no key gate; gray dimmed city; painted
buildings; click a building → card on the right; click plain city or Esc → card closes; chips filter paint;
"Gray map" toggles colour; if `BV` has records, "Color: BHI" appears and recolours. Watch the console for errors.
Note: Cesium tiles only decode while frames render — a backgrounded tab looks black until it is foregrounded.

## Don't

- Don't reintroduce a building list panel (removed by design — the map is the index).
- Don't rely on `scene.pick(...).id` for footprints (wrong-neighbour bug, see above).
- Don't add a build step, framework, or external fetches; keep it one file that opens from disk.
- Don't commit `data/osm_raw.json` (12 MB cache).
- Don't remove the OSM attribution or the Google/Cesium credits.
