"""Read-only audit: overlay extracted wall geometry on the source PDF raster,
and quantify deviation. Writes only new files under ~/plans/.
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

W, H = page.rect.width, page.rect.height
s = s[s[:, [0, 2]].max(1) < W * 0.64]

ymid = (s[:, 1] + s[:, 3]) / 2
hist, edges = np.histogram(ymid, bins=60, range=(0, H))
lo, hi = int(0.30 * 60), int(0.70 * 60)
gap = lo + int(np.argmin(hist[lo:hi]))
SPLIT = edges[gap]

origins = {}
for name, mask in (("level2", s[:, [1, 3]].max(1) < SPLIT),
                    ("level1", s[:, [1, 3]].min(1) >= SPLIT)):
    v = s[mask]
    x0 = v[:, [0, 2]].min()
    y0 = v[:, [1, 3]].min()
    origins[name] = (x0, y0, mask)
    print(name, "x0=%.2f y0=%.2f n=%d" % (x0, y0, len(v)))

print("SPLIT=", SPLIT, "W,H=", W, H)

# render page raster
ZOOM = 2.0  # 144 dpi
mat = pymupdf.Matrix(ZOOM, ZOOM)
pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csRGB)
img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.pixcount if False else 3).copy()
print("rendered", img.shape)

def to_px(x_pt, y_pt):
    return x_pt * ZOOM, y_pt * ZOOM

# darkness mask for later distance-to-nearest-line-pixel computation
gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
dark = (gray < 140).astype(np.uint8) * 255
dark_dt = cv2.distanceTransform(255 - dark, cv2.DIST_L2, 3)  # distance in px to nearest dark pixel
dark_dt_m = dark_dt / ZOOM * M_PER_PT  # px -> pt -> m

files = {
    "level1": ("level1_walls_m.npy", (0, 0, 255)),      # red
    "level2": ("level2_walls_m.npy", (255, 0, 0)),      # blue (pre-align)
}

results = {}
for name, (fn, color) in files.items():
    arr = np.load(os.path.join(OUT, fn))
    x0, y0, _ = origins[name]
    over = img.copy()
    devs = []
    for (X0, Y0, X1, Y1) in arr:
        # invert extract_plan.py transform: m = (v-x0)*M_PER_PT, -(v-y0)*M_PER_PT
        px0 = X0 / M_PER_PT + x0
        py0 = -Y0 / M_PER_PT + y0
        px1 = X1 / M_PER_PT + x0
        py1 = -Y1 / M_PER_PT + y0
        a = to_px(px0, py0)
        b = to_px(px1, py1)
        cv2.line(over, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), color, 2, cv2.LINE_AA)
        # sample along segment in meters directly for deviation measurement
        n = max(2, int(np.hypot(X1 - X0, Y1 - Y0) / 0.1))
        for t in np.linspace(0, 1, n):
            X = X0 + t * (X1 - X0)
            Y = Y0 + t * (Y1 - Y0)
            pxp = X / M_PER_PT + x0
            pyp = -Y / M_PER_PT + y0
            ppx, ppy = to_px(pxp, pyp)
            ix, iy = int(round(ppx)), int(round(ppy))
            if 0 <= iy < dark_dt_m.shape[0] and 0 <= ix < dark_dt_m.shape[1]:
                devs.append(dark_dt_m[iy, ix])
    devs = np.array(devs)
    results[name] = devs
    outpath = os.path.join(OUT, "plan_vs_pdf_%s.png" % name)
    cv2.imwrite(outpath, cv2.cvtColor(over, cv2.COLOR_RGB2BGR))
    print(name, "wrote", outpath, "n_samples=%d" % len(devs),
          "median=%.3fm p90=%.3fm max=%.3fm frac<0.15m=%.1f%%" %
          (np.median(devs), np.percentile(devs, 90), devs.max(), 100 * (devs < 0.15).mean()))

# coverage the other way: fraction of dark (drawn line) pixels near an extracted segment
for name, (fn, color) in files.items():
    x0, y0, mask = origins[name]
    v = s[mask]
    # build extracted-segment distance field in pixel space for this level's crop region
    xs = v[:, [0, 2]]; ys = v[:, [1, 3]]
    x_lo, x_hi = xs.min() - 5, xs.max() + 5
    y_lo, y_hi = ys.min() - 5, ys.max() + 5
    px_lo, py_lo = to_px(x_lo, y_lo)
    px_hi, py_hi = to_px(x_hi, y_hi)
    x_lo_i, x_hi_i = int(min(px_lo, px_hi)), int(max(px_lo, px_hi))
    y_lo_i, y_hi_i = int(min(py_lo, py_hi)), int(max(py_lo, py_hi))
    crop_dark = dark[y_lo_i:y_hi_i, x_lo_i:x_hi_i]
    ext_mask = np.zeros_like(crop_dark)
    arr = np.load(os.path.join(OUT, fn))
    for (X0, Y0, X1, Y1) in arr:
        pxp0 = X0 / M_PER_PT + x0; pyp0 = -Y0 / M_PER_PT + y0
        pxp1 = X1 / M_PER_PT + x0; pyp1 = -Y1 / M_PER_PT + y0
        a = to_px(pxp0, pyp0); b = to_px(pxp1, pyp1)
        cv2.line(ext_mask, (int(a[0]) - x_lo_i, int(a[1]) - y_lo_i),
                  (int(b[0]) - x_lo_i, int(b[1]) - y_lo_i), 255, max(2, int(0.15 * M_PER_PT ** -1 * ZOOM * 0 + 6)), cv2.LINE_AA)
    ext_dt = cv2.distanceTransform(255 - ext_mask, cv2.DIST_L2, 3)
    dark_ys, dark_xs = np.nonzero(crop_dark)
    if len(dark_ys) > 0:
        d_to_ext_m = ext_dt[dark_ys, dark_xs] / ZOOM * M_PER_PT
        cov = 100 * (d_to_ext_m < 0.15).mean()
        print(name, "PDF-dark-pixel coverage within 0.15m of an extracted seg: %.1f%% (n=%d dark px)" % (cov, len(dark_ys)))

print("DONE")
