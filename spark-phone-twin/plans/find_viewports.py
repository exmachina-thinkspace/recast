"""Locate the plan viewports on sheet A-002 by spatial clustering of line work."""
import os
import numpy as np
import pymupdf
import cv2

PDF = os.path.expanduser("~/plans/6986025-CN Approved Plans.pdf")
OUT = os.path.expanduser("~/plans")
PT_FT = 1.0 / 6.75          # feet per pt at 3/32" = 1'-0"

doc = pymupdf.open(PDF)
page = doc[1]
W, H = page.rect.width, page.rect.height

segs = []
for obj in page.get_drawings():
    for it in obj["items"]:
        if it[0] == "l":
            segs.append((it[1].x, it[1].y, it[2].x, it[2].y))
segs = np.array(segs)
L = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
segs = segs[L > 2.0]
print("segments:", len(segs), " page %.0f x %.0f pt" % (W, H))

# coarse occupancy grid over the sheet, then connected components
CELL = 12
gw, gh = int(W // CELL) + 1, int(H // CELL) + 1
grid = np.zeros((gh, gw), np.uint8)
for x0, y0, x1, y1 in segs:
    n = max(2, int(np.hypot(x1 - x0, y1 - y0) / CELL) + 1)
    xs = np.linspace(x0, x1, n) // CELL
    ys = np.linspace(y0, y1, n) // CELL
    grid[ys.astype(int).clip(0, gh - 1), xs.astype(int).clip(0, gw - 1)] = 255

grid = cv2.morphologyEx(grid, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
n, lab, stats, _ = cv2.connectedComponentsWithStats(grid, 8)
order = np.argsort(-stats[1:, cv2.CC_STAT_AREA]) + 1

print("\ntop clusters (sheet coords, pt):")
for r, ci in enumerate(order[:6]):
    x, y, w, h, a = stats[ci]
    X0, Y0 = x * CELL, y * CELL
    Wp, Hp = w * CELL, h * CELL
    print(" #%d  x %4.0f..%4.0f  y %4.0f..%4.0f   %6.1f x %6.1f ft   cells=%d"
          % (r + 1, X0, X0 + Wp, Y0, Y0 + Hp, Wp * PT_FT, Hp * PT_FT, a))

# dump the two biggest as previews so we can eyeball which is which
for r, ci in enumerate(order[:3]):
    x, y, w, h, _ = stats[ci]
    X0, Y0, X1, Y1 = x * CELL, y * CELL, (x + w) * CELL, (y + h) * CELL
    m = ((segs[:, [0, 2]].min(1) >= X0 - 5) & (segs[:, [0, 2]].max(1) <= X1 + 5) &
         (segs[:, [1, 3]].min(1) >= Y0 - 5) & (segs[:, [1, 3]].max(1) <= Y1 + 5))
    s = segs[m]
    if len(s) < 50:
        continue
    SC = 2.2
    iw, ih = int((X1 - X0) * SC) + 20, int((Y1 - Y0) * SC) + 20
    img = np.full((ih, iw, 3), 255, np.uint8)
    for a, b, c, d in s:
        cv2.line(img, (int((a - X0) * SC) + 10, int((b - Y0) * SC) + 10),
                 (int((c - X0) * SC) + 10, int((d - Y0) * SC) + 10), (20, 20, 20), 1, cv2.LINE_AA)
    cv2.imwrite("%s/vp%d.png" % (OUT, r + 1), img)
    np.save("%s/vp%d_segs.npy" % (OUT, r + 1), s)
    print("wrote vp%d.png  (%d segs)" % (r + 1, len(s)))
