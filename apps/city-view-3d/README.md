# Seattle Office Vitals — citywide 3D view

Downtown Seattle office buildings painted onto Google's Photorealistic 3D Tiles, coloured by share leased today and
by **Building Health Index** once scores are attached. Click a building to open its record card. This is the
"Priority 1: Citywide 3D view" of the Recast / Build Vitals demo.

## Run it

Double-click `seattle-office-vitals-3d.html`. That's it — one file, no server, no build. It needs internet for
CesiumJS (CDN) and Google's tiles. The Google Maps Platform key is baked in for the hackathon (it will be deleted
after the event); `?key=YOUR_KEY` in the URL overrides it, and the key gate appears only if the built-in key fails.

## What's in it

- 124 downtown / South Lake Union office buildings with available space (availability report, Aug 2026),
  joined to Seattle Building Energy Benchmarking 2024 (site EUI, ENERGY STAR score, floor area, primary use).
- 116 of them carry an OpenStreetMap footprint (© OpenStreetMap contributors, ODbL) and are painted onto the
  Google mesh; the 8 without one (unbuilt/proposed sites) show as pins.
- Left panel: filters (≤25% leased, ≤10%, no energy value), **Gray map** toggle (desaturated, dimmed imagery so the
  paint reads), legend. Right panel: the building card — BHI block, five vitals with evidence tiers, Street View /
  Google Maps / Fly to, then one section per data source.

## Integrating data (for teammates and their Claude Code)

Read [`CLAUDE.md`](CLAUDE.md) — it documents the data model (`B` buildings, `F` footprints, `BV` Build Vitals
records), the exact BHI record shape (matches `spark-3d-pipeline/src/twin/build_vitals.py` output), and the tools:

```bash
python3 tools/embed_json.py --var BV --merge data/build_vitals_all.json   # attach BHI records → "Color: BHI" appears
python3 tools/embed_json.py --var B  data/buildings.json                  # replace the building list
python3 tools/build_footprints.py                                         # refresh OSM footprints for the list
```

Design rules carried from the Build Vitals brief: every input carries an evidence tier; insufficient evidence shows
gray, never a fake number; weights stay visible.

## Credits

Google Photorealistic 3D Tiles (Map Tiles API) · CesiumJS 1.128 · Building footprints © OpenStreetMap contributors ·
Seattle Building Energy Benchmarking (City of Seattle open data).
