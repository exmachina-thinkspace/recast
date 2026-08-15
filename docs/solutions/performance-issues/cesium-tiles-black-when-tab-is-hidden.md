---
title: Cesium 3D Tiles stay black in a background tab (requestRenderMode + hidden document)
date: 2026-08-15
category: performance-issues
module: city-view-3d
problem_type: performance_issue
component: frontend
symptoms:
  - "Map area black while labels and UI render; tileset.statistics.numberOfTilesProcessing stays constant (e.g. 39) with 0 pending requests"
  - "tileset.tilesLoaded never becomes true; document.hidden === true"
  - "Screenshots taken via browser automation of a non-active tab show a black canvas"
root_cause: async_timing
resolution_type: workflow_improvement
severity: low
framework_version: "cesium 1.128"
tags: [cesium, request-render-mode, background-tab, requestanimationframe, tile-decoding, automation]
---

# Cesium 3D Tiles stay black in a background tab (requestRenderMode + hidden document)

## Problem

While testing through browser automation, the Google tiles never appeared in a Chrome tab even though the page had
loaded, labels rendered and the network was idle. It looked like a rendering bug in the new code.

## Symptoms

- `numberOfTilesProcessing` frozen, `numberOfPendingRequests` 0, canvas black, `document.visibilityState === "hidden"`.

## What Didn't Work

- Calling `viewer.scene.requestRender()` — with no animation frames it changes nothing.

## Solution

Nothing to fix in the page. Cesium decodes tile content across animation frames; browsers pause
`requestAnimationFrame` for hidden tabs/occluded windows, so processing stalls until the tab is visible. To confirm,
pump frames manually: `for (let i = 0; i < 90; i++) { viewer.scene.requestRender(); viewer.render(); }` — processing
dropped 53 → 0 immediately. Test in a visible tab (front the tab, or use the visible browser pane).

## Why This Works

`Model` loading and `Cesium3DTileset` tile processing advance inside `Scene.render`, which is driven by rAF. Hidden
documents get no frames, and `requestRenderMode` does not change that.

## Prevention

- When automating Cesium pages, front the tab before waiting for tiles; check `document.hidden` before diagnosing.
- Treat "black map but UI alive" as a visibility problem first, code problem second.

## Related Issues

- `docs/solutions/developer-experience/single-file-cesium-page-with-inline-data.md`
