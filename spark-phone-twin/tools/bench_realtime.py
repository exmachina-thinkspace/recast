"""Measure what this box can actually sustain per frame, live.

Real-time budgets get quoted from spec sheets and are wrong on a machine that is
already running 14 containers. This measures the real thing: depth + segmentation
latency at the resolutions the phone pipeline actually uses, with a memory guard
so a benchmark never becomes an outage (loading a large model blind wedged this
box once already).

  python bench_realtime.py [--frames 12]
"""
import argparse, subprocess, time
import numpy as np

MIN_FREE_GB = 5


def free_gb():
    try:
        o = subprocess.run(["free", "-g"], capture_output=True, text=True,
                           timeout=5).stdout
        return int([l for l in o.splitlines() if l.startswith("Mem:")][0].split()[6])
    except Exception:
        return 0


def bench(fn, frames, warmup=2):
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(frames):
        t = time.time()
        fn()
        ts.append(time.time() - t)
    ts = np.array(ts)
    return dict(ms=float(ts.mean() * 1000), p90=float(np.percentile(ts, 90) * 1000),
                fps=float(1.0 / ts.mean()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=12)
    a = ap.parse_args()

    print("free memory: %d GiB" % free_gb(), flush=True)
    if free_gb() < MIN_FREE_GB:
        print("ABORT: under %d GiB free, refusing to load models" % MIN_FREE_GB)
        return

    import torch
    dev = "cpu"
    if torch.cuda.is_available():
        try:
            t = torch.zeros(256, 256, device="cuda"); del t
            torch.cuda.empty_cache()
            dev = "cuda"
        except Exception as e:
            print("GPU probe failed (%s) -> CPU" % str(e)[:50])
    print("device: %s | torch %s" % (dev, torch.__version__), flush=True)

    rng = np.random.default_rng(0)
    results = {}

    # ---- YOLO11m-seg: person segmentation, the app's per-frame cost
    try:
        from ultralytics import YOLO
        m = YOLO("yolo11m-seg.pt")
        for res in (448, 640):
            img = rng.integers(0, 255, (res, res, 3), dtype=np.uint8)
            r = bench(lambda: m.predict(img, imgsz=res, classes=[0], conf=0.3,
                                        device=0 if dev == "cuda" else "cpu",
                                        verbose=False), a.frames)
            results["yolo11m-seg @%d" % res] = r
            print("  yolo11m-seg @%d: %.1f ms (p90 %.1f) -> %.1f fps"
                  % (res, r["ms"], r["p90"], r["fps"]), flush=True)
    except Exception as e:
        print("  yolo bench failed: %s" % str(e)[:100], flush=True)

    # ---- Depth-Anything-V2 metric indoor: the geometry cost per phone frame
    if free_gb() >= MIN_FREE_GB:
        try:
            from transformers import pipeline
            from PIL import Image
            print("  loading Depth-Anything-V2-Metric-Indoor-Base ...", flush=True)
            dp = pipeline("depth-estimation",
                          model="depth-anything/Depth-Anything-V2-Metric-Indoor-Base-hf",
                          device=0 if dev == "cuda" else -1)
            for res in (392, 518):
                img = Image.fromarray(rng.integers(0, 255, (res, res, 3), dtype=np.uint8))
                r = bench(lambda: dp(img), max(4, a.frames // 2))
                results["depth-v2-base @%d" % res] = r
                print("  depth-v2-base @%d: %.1f ms (p90 %.1f) -> %.1f fps"
                      % (res, r["ms"], r["p90"], r["fps"]), flush=True)
        except Exception as e:
            print("  depth bench failed: %s" % str(e)[:120], flush=True)
    else:
        print("  skipped depth: memory dropped below guard", flush=True)

    # ---- combined per-phone budget
    print("\n--- sustained budget ---", flush=True)
    d = results.get("depth-v2-base @392")
    y = results.get("yolo11m-seg @448")
    if d and y:
        per = d["ms"] + y["ms"]
        print("depth@392 + yolo@448 = %.0f ms/frame -> %.1f fps for ONE phone"
              % (per, 1000.0 / per))
        for n in (2, 3, 4):
            print("  %d phones round-robin: %.1f fps each" % (n, 1000.0 / (per * n)))
    print("free memory after: %d GiB" % free_gb(), flush=True)


if __name__ == "__main__":
    main()
