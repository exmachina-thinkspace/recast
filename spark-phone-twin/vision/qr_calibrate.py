"""Calibrate reconstruction scale against a QR code of known printed size.

Objects land at the wrong size because two unknowns multiply together: the
camera's true field of view (we assume 68 degrees, which is a guess — an earlier
assumed 120 degrees turned out to be 87 and stretched everything ~1.5x) and the
monocular depth model's metric scale (drifts ~10% per device, and the two depth
models we compared disagreed 16% on absolute distance while agreeing r=0.913 on
shape).

A QR code breaks the ambiguity because we printed it, so its size is not a
guess. For a target of physical width S seen p pixels wide at depth D:

    implied_S = p * D / fx

If implied_S disagrees with the real S, the ratio is the total scale error, and
one multiplicative correction on depth fixes reconstruction, object sizes and
placement together. It does not matter whether the error came from fx or from
the depth model — the product is what distorts the world, and the product is
what this measures.

Also returns the focal length implied if the depth model is taken as truth, so
the two interpretations can be compared rather than conflated.

  python qr_calibrate.py --image frame.jpg --depth depth.npy --size 0.15
"""
import argparse, json, os
import numpy as np
import cv2

# IMPORTANT: this is the width of the black PATTERN, not the printed sheet.
# QR generators add a 4-module white quiet zone, so a "150 mm" printout is
# typically only ~109 mm of pattern — measuring the paper instead of the pattern
# introduces a ~27% scale error, which is larger than the error being corrected.
DEFAULT_QR_M = 0.15          # pattern width in metres; measure the black square
PLAUSIBLE_K = (0.15, 6.0)    # reject corrections outside this - a bad detection
MAX_SKEW = 0.40              # handheld views are oblique; 0.25 rejected normal use


def _wechat():
    """WeChat detector if this OpenCV build ships it; None otherwise."""
    global _WECHAT
    try:
        return _WECHAT
    except NameError:
        pass
    try:
        _WECHAT = cv2.wechat_qrcode_WeChatQRCodeDetector()
    except Exception:
        _WECHAT = None
    return _WECHAT


def _try(det_img, scale=1.0):
    """One detection attempt; corners rescaled back to original pixels."""
    det = cv2.QRCodeDetector()
    txt, pts = "", None
    try:
        txt, pts, _ = det.detectAndDecode(det_img)
    except Exception:
        pts = None
    if pts is None:
        try:
            ok, pts = det.detect(det_img)
            if not ok:
                pts = None
        except Exception:
            pts = None
    if pts is None:
        return None, None
    c = np.asarray(pts, np.float32).reshape(-1, 2)
    if len(c) < 4:
        return None, None
    return c[:4] / float(scale), (txt or "")


def detect_qr(img):
    """Return (corners 4x2, decoded_text) or (None, None).

    Escalates through progressively more expensive preprocessing. Returning on
    the first hit keeps the common case at roughly the cost of a single detect.
    """
    if img is None:
        return None, None

    c, t = _try(img)
    if c is not None:
        return c, t

    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    c, t = _try(g)
    if c is not None:
        return c, t

    # Uneven hallway lighting flattens the pattern's contrast.
    try:
        eq = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(g)
        c, t = _try(eq)
        if c is not None:
            return c, t
    except Exception:
        eq = g

    # A 12.5 cm code across a room is only tens of pixels wide; upscaling gives
    # the finder patterns enough resolution to register.
    for sc in (2.0, 3.0):
        try:
            big = cv2.resize(eq, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
            c, t = _try(big, scale=sc)
            if c is not None:
                return c, t
        except Exception:
            pass

    w = _wechat()
    if w is not None:
        for cand in (img, eq):
            try:
                texts, pts = w.detectAndDecode(cand)
            except Exception:
                continue
            if pts is not None and len(pts):
                c = np.asarray(pts[0], np.float32).reshape(-1, 2)
                if len(c) >= 4:
                    return c[:4], (texts[0] if len(texts) else "")
    return None, None


def qr_pixel_size(corners):
    """Side length in pixels, averaged over the quad's four edges."""
    c = np.asarray(corners, np.float64)
    d = [float(np.linalg.norm(c[i] - c[(i + 1) % 4])) for i in range(4)]
    return float(np.mean(d)), float(np.std(d))


def calibrate(img, depth, qr_size_m=DEFAULT_QR_M, hfov_deg=68.0):
    """Scale correction from a known-size QR in view.

    Returns dict or None. `scale_k` multiplies depth (and therefore every
    reconstructed dimension); `hfov_implied_deg` is what the field of view would
    be if the depth model were exactly right instead.
    """
    if img is None or depth is None:
        return None
    corners, text = detect_qr(img)
    if corners is None:
        return None
    Hh, Ww = img.shape[:2]
    if depth.shape != (Hh, Ww):
        depth = cv2.resize(depth.astype(np.float32), (Ww, Hh),
                           interpolation=cv2.INTER_LINEAR)
    p_px, p_std = qr_pixel_size(corners)
    if p_px < 12:
        return None                      # too small on screen to measure from
    # a very non-square quad means a steep oblique view; the side-length average
    # stops being a reliable proxy for the true width
    skew = p_std / max(p_px, 1e-6)
    cx, cy = corners[:, 0].mean(), corners[:, 1].mean()
    r = max(3, int(p_px * 0.25))
    x0, x1 = int(max(0, cx - r)), int(min(Ww, cx + r))
    y0, y1 = int(max(0, cy - r)), int(min(Hh, cy + r))
    patch = depth[y0:y1, x0:x1]
    patch = patch[np.isfinite(patch) & (patch > 0.15) & (patch < 25.0)]
    if patch.size < 12:
        return None
    D = float(np.median(patch))

    fx = Ww / (2.0 * np.tan(np.radians(hfov_deg) / 2.0))
    implied_S = p_px * D / fx
    if implied_S <= 1e-6:
        return None
    k = float(qr_size_m) / implied_S
    # if the depth model is taken as correct, this is the focal length that
    # would make the QR measure its true size
    fx_implied = p_px * D / float(qr_size_m)
    hfov_implied = float(np.degrees(2.0 * np.arctan(Ww / (2.0 * fx_implied))))

    return dict(
        scale_k=round(float(k), 4),
        implied_qr_size_m=round(float(implied_S), 4),
        true_qr_size_m=float(qr_size_m),
        qr_pixels=round(p_px, 1), qr_skew=round(skew, 3),
        depth_at_qr_m=round(D, 3),
        hfov_assumed_deg=float(hfov_deg),
        hfov_implied_deg=round(hfov_implied, 1),
        text=text[:80],
        usable=bool(PLAUSIBLE_K[0] <= k <= PLAUSIBLE_K[1] and skew < MAX_SKEW),
        reject=("" if (PLAUSIBLE_K[0] <= k <= PLAUSIBLE_K[1] and skew < MAX_SKEW)
                else ("k=%.3f out of %.2f..%.2f" % (k, *PLAUSIBLE_K)
                      if not (PLAUSIBLE_K[0] <= k <= PLAUSIBLE_K[1])
                      else "skew=%.3f >= %.2f (too oblique)" % (skew, MAX_SKEW))),
        note=("scale_k multiplies depth; hfov_implied is the alternative reading "
              "if the depth model is trusted instead"))


def accumulate(results, min_n=3):
    """Median of several sightings — one frame is a sample, not a calibration."""
    good = [r for r in results if r and r.get("usable")]
    if len(good) < min_n:
        return None
    ks = np.array([r["scale_k"] for r in good], np.float64)
    fs = np.array([r["hfov_implied_deg"] for r in good], np.float64)
    return dict(scale_k=round(float(np.median(ks)), 4),
                scale_k_spread=round(float(np.percentile(ks, 84) -
                                           np.percentile(ks, 16)), 4),
                hfov_implied_deg=round(float(np.median(fs)), 1),
                samples=len(good))


def save(cal, path=None):
    path = path or os.path.expanduser("~/plans/qr_calibration.json")
    tmp = path + ".tmp"
    json.dump(cal, open(tmp, "w"), indent=1)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="")
    ap.add_argument("--depth", default="")
    ap.add_argument("--size", type=float, default=DEFAULT_QR_M)
    ap.add_argument("--hfov", type=float, default=68.0)
    a = ap.parse_args()

    if a.image:
        img = cv2.imread(a.image)
        d = np.load(a.depth) if a.depth else None
        print(json.dumps(calibrate(img, d, a.size, a.hfov), indent=1))
        raise SystemExit(0)

    # synthetic check: render a QR at a known distance with a known true FOV,
    # then confirm the recovered correction matches the error we injected
    try:
        import qrcode
        # border=0: render the pattern alone, so the rendered pixel width IS the
        # pattern width. With the default border the detector measures ~72% of
        # the image and the calibration silently inherits that ratio.
        _qc = qrcode.QRCode(border=0)
        _qc.add_data("https://example/?a=a01")
        _qc.make(fit=True)
        q = np.array(_qc.make_image().convert("RGB"))
        q = cv2.cvtColor(q, cv2.COLOR_RGB2BGR)
    except Exception:
        q = np.full((300, 300, 3), 255, np.uint8)
        cv2.rectangle(q, (30, 30), (120, 120), (0, 0, 0), -1)
        cv2.rectangle(q, (180, 30), (270, 120), (0, 0, 0), -1)
        cv2.rectangle(q, (30, 180), (120, 270), (0, 0, 0), -1)
        print("(qrcode module missing; detector test will likely fail)")

    # 0.8 m, not 2 m: at 2 m a 150 mm QR is only ~59 px wide and OpenCV's
    # detector misses it entirely — a real constraint on how far away a phone
    # can calibrate from, worth knowing rather than designing around blindly
    W_img, H_img = 1280, 720
    TRUE_HFOV, TRUE_D, TRUE_S = 78.0, 0.8, 0.15
    fx_true = W_img / (2.0 * np.tan(np.radians(TRUE_HFOV) / 2.0))
    px = int(round(fx_true * TRUE_S / TRUE_D))
    img = np.full((H_img, W_img, 3), 255, np.uint8)
    qs = cv2.resize(q, (px, px), interpolation=cv2.INTER_NEAREST)
    ox, oy = (W_img - px) // 2, (H_img - px) // 2
    img[oy:oy + px, ox:ox + px] = qs
    depth = np.full((H_img, W_img), TRUE_D, np.float32)

    print("rendered a %.0f mm QR at %.1f m, %d px wide, true hFOV %.0f deg"
          % (TRUE_S * 1000, TRUE_D, px, TRUE_HFOV))
    r = calibrate(img, depth, qr_size_m=TRUE_S, hfov_deg=68.0)
    if r is None:
        print("QR NOT DETECTED — cannot self-test the maths on this build")
        raise SystemExit(0)
    print("assumed hFOV 68 deg -> implied QR size %.3f m (true %.3f)"
          % (r["implied_qr_size_m"], TRUE_S))
    print("scale correction k = %.3f   implied hFOV = %.1f deg (true %.0f)"
          % (r["scale_k"], r["hfov_implied_deg"], TRUE_HFOV))
    assert abs(r["hfov_implied_deg"] - TRUE_HFOV) < 2.0, "hFOV recovery off"
    # Direction matters and is easy to get backwards: assuming a NARROWER field
    # of view than the truth means a larger assumed focal length, so the world is
    # reconstructed too SMALL and the correction must grow it. (68 assumed vs 78
    # true -> k > 1.) The opposite assumption inflates the world instead.
    assert r["scale_k"] > 1.0, "assuming a narrower FOV should grow the world"

    # and the reverse case, so the sign is pinned in both directions
    r2 = calibrate(img, depth, qr_size_m=TRUE_S, hfov_deg=95.0)
    print("assumed hFOV 95 deg -> k = %.3f (expect < 1, world was too large)"
          % r2["scale_k"])
    assert r2["scale_k"] < 1.0, "assuming a wider FOV should shrink the world"
    print("\nSELF-TEST OK")
