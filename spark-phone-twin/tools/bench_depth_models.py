"""Validate YOLO26-depth against Depth-Anything-V2 on real frames of this building.

Ultralytics advertises YOLO26-depth as metric and much faster, but publishes no
apples-to-apples accuracy comparison against DA2 — their docs say so outright.
Speed is worthless if the metres are wrong, so this measures both on actual
indoor frames from the site rather than synthetic noise:

  speed      mean/p90 latency per model
  agreement  Pearson r and median scale ratio between the two depth maps
  sanity     absolute range — an indoor frame should read ~0.3-20 m

A high r with a scale ratio far from 1.0 means "right shape, wrong metres",
which the floor plan can correct. A low r means the geometry itself disagrees
and the faster model can't be trusted.

  python bench_depth_models.py [--img ~/arlo-frames/lobby.jpg]
"""
import argparse, glob, os, subprocess, time
import numpy as np

MIN_FREE_GB = 5


def free_gb():
    try:
        o = subprocess.run(["free", "-g"], capture_output=True, text=True,
                           timeout=5).stdout
        return int([l for l in o.splitlines() if l.startswith("Mem:")][0].split()[6])
    except Exception:
        return 0


def stats(name, d):
    d = np.asarray(d, np.float32)
    f = d[np.isfinite(d) & (d > 0)]
    if not len(f):
        print("  %-22s no finite depth" % name); return None
    print("  %-22s min %.2f  med %.2f  p95 %.2f  max %.2f m"
          % (name, f.min(), np.median(f), np.percentile(f, 95), f.max()))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default="")
    ap.add_argument("--frames", type=int, default=8)
    a = ap.parse_args()

    img_path = a.img or next(
        (p for p in (os.path.expanduser("~/arlo-frames/lobby.jpg"),
                     os.path.expanduser("~/arlo-frames/common.jpg"))
         if os.path.exists(p)),
        (glob.glob(os.path.expanduser("~/arlo-frames/*.jpg")) or [""])[0])
    if not img_path or not os.path.exists(img_path):
        print("no test image found"); return
    print("image: %s | free %d GiB" % (img_path, free_gb()), flush=True)
    if free_gb() < MIN_FREE_GB:
        print("ABORT: under %d GiB free" % MIN_FREE_GB); return

    import cv2, torch
    bgr = cv2.imread(img_path)
    print("shape: %s" % (bgr.shape,), flush=True)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        t = torch.zeros(256, 256, device=dev); del t; torch.cuda.empty_cache()
    except Exception:
        dev = "cpu"
    print("device: %s\n" % dev, flush=True)

    d_yolo = d_da2 = None

    # ---- YOLO26-depth: does it even exist in this ultralytics?
    print("[YOLO26-depth]", flush=True)
    try:
        from ultralytics import YOLO
        ym = None
        for cand in ("yolo26s-depth.pt", "yolo26n-depth.pt"):
            try:
                ym = YOLO(cand)
                print("  loaded %s" % cand, flush=True)
                break
            except Exception as e:
                print("  %s unavailable: %s" % (cand, str(e).splitlines()[0][:80]),
                      flush=True)
        if ym is not None:
            r = ym.predict(bgr, device=0 if dev == "cuda" else "cpu", verbose=False)[0]
            arr = None
            for attr in ("depth", "depths"):
                o = getattr(r, attr, None)
                if o is not None:
                    arr = o.data.cpu().numpy() if hasattr(o, "data") else np.asarray(o)
                    break
            if arr is None:
                print("  loaded but exposes no .depth on Results — unusable", flush=True)
            else:
                d_yolo = stats("yolo26-depth", np.squeeze(arr))
                ts = []
                for _ in range(a.frames):
                    t0 = time.time()
                    ym.predict(bgr, device=0 if dev == "cuda" else "cpu", verbose=False)
                    ts.append(time.time() - t0)
                ts = np.array(ts)
                print("  speed: %.1f ms (p90 %.1f) -> %.1f fps"
                      % (ts.mean() * 1e3, np.percentile(ts, 90) * 1e3, 1 / ts.mean()),
                      flush=True)
    except Exception as e:
        print("  failed: %s" % str(e).splitlines()[0][:120], flush=True)

    # ---- Depth-Anything-V2 metric indoor: the accuracy-checked baseline
    print("\n[Depth-Anything-V2-Metric-Indoor-Base]", flush=True)
    if free_gb() >= MIN_FREE_GB:
        try:
            from transformers import pipeline
            from PIL import Image
            dp = pipeline("depth-estimation",
                          model="depth-anything/Depth-Anything-V2-Metric-Indoor-Base-hf",
                          device=0 if dev == "cuda" else -1)
            pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            out = dp(pil)["predicted_depth"]
            d_da2 = stats("depth-anything-v2", np.squeeze(np.array(out, np.float32)))
            ts = []
            for _ in range(max(4, a.frames // 2)):
                t0 = time.time(); dp(pil); ts.append(time.time() - t0)
            ts = np.array(ts)
            print("  speed: %.1f ms (p90 %.1f) -> %.1f fps"
                  % (ts.mean() * 1e3, np.percentile(ts, 90) * 1e3, 1 / ts.mean()),
                  flush=True)
        except Exception as e:
            print("  failed: %s" % str(e).splitlines()[0][:120], flush=True)

    # ---- agreement
    print("\n[agreement]", flush=True)
    if d_yolo is None or d_da2 is None:
        print("  skipped: need both models to compare", flush=True)
    else:
        h, w = d_da2.shape[:2]
        y = cv2.resize(d_yolo.astype(np.float32), (w, h))
        m = np.isfinite(y) & np.isfinite(d_da2) & (y > 0) & (d_da2 > 0)
        if m.sum() < 500:
            print("  too few valid pixels", flush=True)
        else:
            r = float(np.corrcoef(y[m], d_da2[m])[0, 1])
            ratio = float(np.median(d_da2[m] / y[m]))
            print("  pearson r      : %.3f" % r)
            print("  scale (DA2/Y26): %.3f" % ratio)
            print("  -> %s" % ("shapes agree; scale differs, plan can correct"
                               if r > 0.8 and abs(np.log(ratio)) > 0.15 else
                               "strong agreement" if r > 0.8 else
                               "GEOMETRY DISAGREES - do not swap"), flush=True)
    print("\nfree after: %d GiB" % free_gb(), flush=True)


if __name__ == "__main__":
    main()
