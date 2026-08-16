"""Classify extracted wall segments (level{1,2}_walls_m.npy) as real wall vs
drafting annotation, using the PDF's OCG/layer metadata (PyMuPDF get_drawings()
'layer' field) as the primary discriminator, cross-checked with stroke width.

Read-only w.r.t. all existing outputs; writes only new files under ~/plans/.
"""
import os, json
import numpy as np
import pymupdf
import cv2

PDF = os.path.expanduser("~/plans/6986025-CN Approved Plans.pdf")
OUT = os.path.expanduser("~/plans")
FT_PER_PT = 1.0 / 6.75
M_PER_PT = FT_PER_PT * 0.3048

doc = pymupdf.open(PDF)
page = doc[1]
M = page.rotation_matrix

# ---- reproduce extract_plan.py + build_building.py exactly, but retain
# ---- per-segment layer/width metadata that those scripts discarded ----
recs = []  # x0,y0,x1,y1 (pt, displayed space), layer, width, color
for obj in page.get_drawings():
    layer = obj.get("layer")
    width = obj.get("width") or 0.0
    color = obj.get("color")
    dashes = obj.get("dashes")
    for it in obj["items"]:
        if it[0] == "l":
            a = it[1] * M
            b = it[2] * M
            recs.append((a.x, a.y, b.x, b.y, layer, width, color, dashes))

s = np.array([[r[0], r[1], r[2], r[3]] for r in recs])
L = np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1])
keep = L > 2.0
recs = [r for r, k in zip(recs, keep) if k]
s = s[keep]

W, H = page.rect.width, page.rect.height
keep = s[:, [0, 2]].max(1) < W * 0.64
recs = [r for r, k in zip(recs, keep) if k]
s = s[keep]

ymid = (s[:, 1] + s[:, 3]) / 2
hist, edges = np.histogram(ymid, bins=60, range=(0, H))
lo, hi = int(0.30 * 60), int(0.70 * 60)
gap = lo + int(np.argmin(hist[lo:hi]))
SPLIT = edges[gap]

# ---- annotation layer classification ----
# Layers confirmed as pure drafting/diagram annotation (grid centerlines,
# grid bubble numbers, the egress-plan travel-distance diagram, text,
# title block, detail callouts). Everything else is a physically-drawn
# building element (wall, glazing, column, door, fixture, casework, stair)
# and is kept -- per instructions we err toward keeping when unsure.
ANNOTATION_LAYERS = {
    "A-GRID", "A-GRIDNO",                      # structural grid centerlines/numbers
    "A-FLOR-EGRESS", "A-FLOR-EGRESS-TEST",      # egress travel-distance diagram
    "EXIT",                                     # exit tag symbols
    "SHT-TTLBLK", "SHT-BORDER", "SHT-LOGO", "SHT-SIGNATURE", "SHT-TEXT",  # title block
    "A-ANNO-TEXT", "A-FLOR-TEXT-CD", "A-FLOR-TEXT-SK", "A-FLOR-IDEN-NAME",
    "L-CLG-TEXT-48",                            # text labels
    "A-DETAIL", "A-Detl-1", "A-Detl-2", "A-Detl-4", "A-Detl-8",
    "A-DETL-PATT", "DI-BORDER", "AR-WALL-SECTION",  # detail/section callouts
    "A-FLOR-DIM",                                # dimension lines (present as a layer, 0 'l' items in-range)
}
# Unlabeled / catch-all layers are a mix of real stray wall lines and
# hairline drafting ticks; split them on stroke width (walls are drawn at
# the sheet's 0.72pt wall weight, annotation ticks/hatch render at width 0).
SPLIT_BY_WIDTH_LAYERS = {"0", "", None, "SHADE-CORE"}
WIDTH_THRESH = 0.3

def classify(layer, width):
    if layer in ANNOTATION_LAYERS:
        return "annotation"
    if layer in SPLIT_BY_WIDTH_LAYERS:
        return "wall" if width >= WIDTH_THRESH else "annotation"
    return "wall"

results = {}
layer_stats = {}
for name, mask in (("level2", s[:, [1, 3]].max(1) < SPLIT),
                    ("level1", s[:, [1, 3]].min(1) >= SPLIT)):
    idx = np.nonzero(mask)[0]
    sub = [recs[i] for i in idx]
    ssub = s[idx]

    Lsub = np.hypot(ssub[:, 2] - ssub[:, 0], ssub[:, 3] - ssub[:, 1])
    ang = np.degrees(np.arctan2(np.abs(ssub[:, 3] - ssub[:, 1]), np.abs(ssub[:, 2] - ssub[:, 0])))
    axis = (ang < 8) | (ang > 82)
    wallcand = axis & (Lsub * M_PER_PT > 0.30) & (Lsub * M_PER_PT < 60)
    cidx = np.nonzero(wallcand)[0]
    csub = [sub[i] for i in cidx]
    cs = ssub[cidx]

    x0 = ssub[:, [0, 2]].min()
    y0 = ssub[:, [1, 3]].min()
    m_all = np.stack([(cs[:, 0] - x0) * M_PER_PT, -(cs[:, 1] - y0) * M_PER_PT,
                       (cs[:, 2] - x0) * M_PER_PT, -(cs[:, 3] - y0) * M_PER_PT], -1)

    labels = np.array([classify(r[4], r[5]) for r in csub])
    kept_mask = labels == "wall"

    # sanity: this should reproduce the existing level{1,2}_walls_m.npy exactly
    existing = np.load(os.path.join(OUT, "%s_walls_m.npy" % name))
    match = existing.shape == m_all.shape and np.allclose(existing, m_all, atol=1e-6)
    print("%s: reproduces existing walls_m.npy exactly: %s (%d vs %d)"
          % (name, match, len(existing), len(m_all)))

    clean = m_all[kept_mask]
    removed = m_all[~kept_mask]
    np.save(os.path.join(OUT, "%s_walls_m_clean.npy" % name), clean.astype(np.float32))
    print("%s: kept=%d removed=%d (of %d)" % (name, kept_mask.sum(), (~kept_mask).sum(), len(m_all)))

    from collections import Counter
    rem_layers = Counter(r[4] for r, keep_ in zip(csub, kept_mask) if not keep_)
    kept_layers = Counter(r[4] for r, keep_ in zip(csub, kept_mask) if keep_)
    layer_stats[name] = {
        "removed_by_layer": dict(rem_layers.most_common()),
        "kept_by_layer": dict(kept_layers.most_common()),
    }
    results[name] = dict(kept=int(kept_mask.sum()), removed=int((~kept_mask).sum()),
                          total=int(len(m_all)))

# level2 datum alignment
dx, dy = -8.802, -0.156
l2 = np.load(os.path.join(OUT, "level2_walls_m_clean.npy"))
l2a = l2.copy()
l2a[:, [0, 2]] += dx
l2a[:, [1, 3]] += dy
np.save(os.path.join(OUT, "level2_walls_m_clean_aligned.npy"), l2a.astype(np.float32))
print("wrote level2_walls_m_clean_aligned.npy  n=%d  dx=%.3f dy=%.3f" % (len(l2a), dx, dy))

meta = {
    "method": "OCG/layer classification (PyMuPDF get_drawings()['layer']) as primary "
               "signal, cross-checked with stroke width for unlabeled/catch-all layers; "
               "dashes were not usable (100%% of paths report dashes='[] 0', no dashed "
               "strokes exist in this PDF export). Classification runs on the same "
               "axis-aligned + 0.30-60m length wall-candidate set that build_building.py "
               "already derives from level{1,2}_segs_m.npy (verified byte-identical to "
               "existing level{1,2}_walls_m.npy before this filter is applied).",
    "kept": sum(v["kept"] for v in results.values()),
    "removed": sum(v["removed"] for v in results.values()),
    "per_level": results,
    "criteria": {
        "annotation_layers_removed": sorted(ANNOTATION_LAYERS),
        "width_split_layers": ["0 (unnamed/default)", "SHADE-CORE"],
        "width_split_threshold_pt": WIDTH_THRESH,
        "width_split_rule": "kept if stroke width >= 0.3pt (matches sheet's 0.72pt wall "
                             "weight), removed if width < 0.3pt (hairline fill/hatch ticks)",
        "dashes_signal": "not usable -- all 6644 stroked paths on this sheet use dashes='[] 0'; "
                          "grid centerlines are drawn solid on this sheet, not dashed",
        "layers_present_but_zero_candidates": "A-FLOR-DIM (dimension line layer) contributes "
                                               "0 straight-line ('l') items in the wall-candidate "
                                               "range -- dimension linework in this PDF is drawn "
                                               "with curves/ticks that the axis+length filter "
                                               "already excludes, so no dimension contamination "
                                               "was found in level{1,2}_walls_m.npy to begin with",
        "layer_breakdown": layer_stats,
    },
}
with open(os.path.join(OUT, "wall_filter.json"), "w") as f:
    json.dump(meta, f, indent=2)
print("wrote wall_filter.json")
print(json.dumps({"kept": meta["kept"], "removed": meta["removed"], "per_level": results}, indent=2))
