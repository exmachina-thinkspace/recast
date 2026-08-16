"""Extract Level 1 / Level 2 wall geometry from sheet A-002.

The page carries /Rotate 270, so get_drawings() coordinates are in UNROTATED
space and must be pushed through page.rotation_matrix to match the sheet as
drawn. Scale 3/32" = 1'-0"  ->  6.75 pt per foot.
"""
import os
import numpy as np
import pymupdf
import cv2

PDF = os.path.expanduser("~/plans/6986025-CN Approved Plans.pdf")
OUT = os.path.expanduser("~/plans")
FT_PER_PT = 1.0 / 6.75
M_PER_PT = FT_PER_PT * 0.3048

doc = pymupdf.open(PDF)
page = doc[1]
M = page.rotation_matrix          # unrotated -> displayed

pts = []
for obj in page.get_drawings():
    for it in obj["items"]:
        if it[0] == "l":
            a = it[1] * M
            b = it[2] * M
            pts.append((a.x, a.y, b.x, b.y))
s = np.array(pts)
L = np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1])
s = s[L > 2.0]
print("segments:", len(s))

W, H = page.rect.width, page.rect.height
print("displayed page: %.0f x %.0f pt" % (W, H))

# drop the right-hand title/notes panel
s = s[s[:, [0, 2]].max(1) < W * 0.64]

# find the horizontal gap between the two stacked viewports
ymid = (s[:, 1] + s[:, 3]) / 2
hist, edges = np.histogram(ymid, bins=60, range=(0, H))
lo, hi = int(0.30 * 60), int(0.70 * 60)
gap = lo + int(np.argmin(hist[lo:hi]))
SPLIT = edges[gap]
print("split at y = %.0f pt (bin count %d)" % (SPLIT, hist[gap]))

for name, mask in (("level2", s[:, [1, 3]].max(1) < SPLIT),
                   ("level1", s[:, [1, 3]].min(1) >= SPLIT)):
    v = s[mask]
    if len(v) < 100:
        print("%s: only %d segs, skipped" % (name, len(v)))
        continue
    x0 = v[:, [0, 2]].min()
    y0 = v[:, [1, 3]].min()
    m = np.stack([(v[:, 0] - x0) * M_PER_PT, -(v[:, 1] - y0) * M_PER_PT,
                  (v[:, 2] - x0) * M_PER_PT, -(v[:, 3] - y0) * M_PER_PT], -1)
    w = m[:, [0, 2]].max() - m[:, [0, 2]].min()
    h = m[:, [1, 3]].max() - m[:, [1, 3]].min()
    print("%s: %d segs   %.1f x %.1f m   (%.0f x %.0f ft)   footprint<=%.0f sqft"
          % (name, len(m), w, h, w / .3048, h / .3048, (w * h) / .3048 ** 2))
    np.save("%s/%s_segs_m.npy" % (OUT, name), m)

    SC = 30
    iw, ih = int(w * SC) + 40, int(h * SC) + 40
    img = np.full((ih, iw, 3), 255, np.uint8)
    ox, oy = -m[:, [0, 2]].min(), -m[:, [1, 3]].min()
    for a, b, c, d in m:
        cv2.line(img, (int((a + ox) * SC) + 20, ih - 20 - int((b + oy) * SC)),
                 (int((c + ox) * SC) + 20, ih - 20 - int((d + oy) * SC)),
                 (25, 25, 25), 1, cv2.LINE_AA)
    cv2.imwrite("%s/%s_plan.png" % (OUT, name), img)
print("done")
