#!/usr/bin/env python3
"""Fix #1 (this session): remove A-FLOR-FIXT-layer linework that filter_walls.py's
default "keep if unsure" policy classified as wall.

Adversarial finding (via plan_overlay_verify.py round 1): the 20 worst-offending
extracted "wall" segments on level 2 (mean forward distance 80-150px @200dpi,
i.e. 1.3-2.4m from any drawn ink) all trace back byte-exact to raw PDF strokes on
OCG layer "A-FLOR-FIXT" (floor fixture/equipment outline) -- large freestanding
rectangle(s), not walls. A-FLOR-FIXT is not in filter_walls.py's ANNOTATION_LAYERS
exclusion set and is a named (non-"0") layer, so classify() defaulted it to "wall"
unconditionally. level1 has only 1 such stroke (negligible); level2 has 20,
forming multi-metre rectangles that visibly do not track any drawn wall.

This script reproduces filter_walls.py's exact pipeline (verified byte-identical
below) purely to recover the per-segment layer label that classify() itself
discarded, then drops A-FLOR-FIXT specifically -- nothing else. Writes NEW files
only: level{1,2}_walls_m_v3.npy (v3 = clean, A-FLOR-FIXT removed, level2 shifted
by the existing, unchanged level_alignment.json dx/dy so it drops in as a
same-datum replacement for level2_walls_m_clean_aligned.npy).

Read-only w.r.t. every existing file.
"""
import os, json
import numpy as np
import pymupdf

PDF = os.path.expanduser("~/plans/6986025-CN Approved Plans.pdf")
OUT = os.path.expanduser("~/plans")
FT_PER_PT = 1.0 / 6.75
M_PER_PT = FT_PER_PT * 0.3048

DROP_LAYERS = {"A-FLOR-FIXT"}

doc = pymupdf.open(PDF)
page = doc[1]
M = page.rotation_matrix

recs = []
for obj in page.get_drawings():
    layer = obj.get("layer")
    width = obj.get("width") or 0.0
    for it in obj["items"]:
        if it[0] == "l":
            a = it[1] * M
            b = it[2] * M
            recs.append((a.x, a.y, b.x, b.y, layer, width))

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

ANNOTATION_LAYERS = {
    "A-GRID", "A-GRIDNO", "A-FLOR-EGRESS", "A-FLOR-EGRESS-TEST", "EXIT",
    "SHT-TTLBLK", "SHT-BORDER", "SHT-LOGO", "SHT-SIGNATURE", "SHT-TEXT",
    "A-ANNO-TEXT", "A-FLOR-TEXT-CD", "A-FLOR-TEXT-SK", "A-FLOR-IDEN-NAME",
    "L-CLG-TEXT-48", "A-DETAIL", "A-Detl-1", "A-Detl-2", "A-Detl-4", "A-Detl-8",
    "A-DETL-PATT", "DI-BORDER", "AR-WALL-SECTION", "A-FLOR-DIM",
}
SPLIT_BY_WIDTH_LAYERS = {"0", "", None, "SHADE-CORE"}
WIDTH_THRESH = 0.3


def classify(layer, width):
    if layer in ANNOTATION_LAYERS:
        return "annotation"
    if layer in SPLIT_BY_WIDTH_LAYERS:
        return "wall" if width >= WIDTH_THRESH else "annotation"
    return "wall"


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

    existing_clean = np.load(os.path.join(OUT, "%s_walls_m_clean.npy" % name))
    reproduced = m_all[kept_mask].astype(np.float32)
    match = existing_clean.shape == reproduced.shape and np.allclose(existing_clean, reproduced, atol=1e-4)
    print("%s: pipeline reproduces existing _clean.npy exactly: %s (%d vs %d)" %
          (name, match, len(existing_clean), len(reproduced)))
    assert match, "pipeline mismatch -- refusing to derive v3 from an unverified reproduction"

    fixt_mask = np.array([r[4] in DROP_LAYERS for r in csub])
    drop_mask = kept_mask & fixt_mask
    v3_mask = kept_mask & ~fixt_mask
    print("%s: dropping %d A-FLOR-FIXT segment(s) out of %d kept wall segments -> %d remain" %
          (name, int(drop_mask.sum()), int(kept_mask.sum()), int(v3_mask.sum())))

    v3 = m_all[v3_mask].astype(np.float32)
    np.save(os.path.join(OUT, "%s_walls_m_v3_unaligned.npy" % name), v3)

with open(os.path.join(OUT, "level_alignment.json")) as f:
    al = json.load(f)
dx, dy = al["level2"]["dx"], al["level2"]["dy"]

l1v3 = np.load(os.path.join(OUT, "level1_walls_m_v3_unaligned.npy"))
np.save(os.path.join(OUT, "level1_walls_m_v3.npy"), l1v3)

l2v3 = np.load(os.path.join(OUT, "level2_walls_m_v3_unaligned.npy"))
l2v3a = l2v3.copy()
l2v3a[:, [0, 2]] += dx
l2v3a[:, [1, 3]] += dy
np.save(os.path.join(OUT, "level2_walls_m_v3.npy"), l2v3a.astype(np.float32))
print("wrote level1_walls_m_v3.npy (n=%d) and level2_walls_m_v3.npy (n=%d, dx=%.3f dy=%.3f, "
      "same alignment as existing level_alignment.json -- unchanged, not recomputed)" %
      (len(l1v3), len(l2v3a), dx, dy))
