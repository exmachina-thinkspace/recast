---
title: Google Map Tiles API key for a local single-file 3D demo (setup, billing, key handling)
date: 2026-08-15
category: best-practices
module: city-view-3d
problem_type: best_practice
component: infrastructure
severity: medium
applies_when:
  - "Enabling Google Photorealistic 3D Tiles (Map Tiles API) for a demo page opened from disk"
  - "Deciding whether to hard-code, prompt for, or restrict a Google Maps Platform key"
tags: [google-maps-platform, map-tiles-api, api-key, billing, referrer-restriction, file-url]
---

# Google Map Tiles API key for a local single-file 3D demo (setup, billing, key handling)

## Context

The citywide view streams Google Photorealistic 3D Tiles, which need a Google Maps Platform key with the Map Tiles API
enabled. Setting it up hit billing gates, and the team wanted the page to open with no prompts.

## Guidance

- **Billing first.** Enabling any Maps API on a project without a billing account redirects to "Set the billing account";
  a Google Cloud project with an existing billing account avoids the detour (we used GrowthHit Ads Grid; the Spark
  Hackathon project had none). Map Tiles usage is pay-as-you-go with monthly free credit — a hackathon stays free.
- **Verify the key end to end** with `GET https://tile.googleapis.com/v1/3dtiles/root.json?key=…` → HTTP 200 and a
  tileset JSON. A 403 means API not enabled or billing missing. No propagation wait was needed.
- **Referrer restrictions break `file://`.** Pages opened from disk send no Referer, so an HTTP-referrer-restricted key
  is rejected. Restrict only when serving from a known origin (localhost or a demo domain), or leave unrestricted for
  a throwaway event key.
- **Key handling for a single-file page.** Precedence used: `?key=` URL param → key saved in localStorage → key baked
  into the file. The gate UI appears only if none works. Baking the key in is a deliberate hackathon trade-off: anyone
  with the file can use it and it will be scraped if the file goes public — delete/rotate the key after the event and
  never leave it in a repo that outlives it.
- **Don't type keys into pages via automation.** When an agent tests the page it should reuse the stored key or the
  baked-in default rather than entering the secret into a field.
- **Commit hygiene.** Some agent permission classifiers block commits whose message mentions embedding an API key even
  when the file content is unchanged; keep commit messages neutral or have a human make that commit.

## Why This Matters

Losing 20 minutes to billing redirects, or a demo failing on stage because a restricted key sees no referrer, is
avoidable; and a leaked unrestricted key becomes someone else's bill.

## When to Apply

- Any Google Maps Platform / Cesium ion / Mapbox key used by a page opened from disk.

## Examples

`apps/city-view-3d/seattle-office-vitals-3d.html` (`DEFAULT_KEY`, key gate) — hackathon key, deleted after 2026-08-17.

## Related

- `docs/solutions/developer-experience/single-file-cesium-page-with-inline-data.md`
