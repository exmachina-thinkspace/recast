"""Recast image generation client -- FLUX.1-dev on the Spark (NIM) or hosted.

Turns a "Recast opportunity" (what a space could become) into a rendered
image. Two interchangeable backends behind one function call:

  nim     FLUX.1-dev NVIDIA NIM running locally on the Acer GN100 / DGX Spark
          (see run-nim.sh). Fully on-box; this is the judge-facing path.
  hosted  The same model served by build.nvidia.com (needs NVIDIA_API_KEY).
          Use it to build UI/integration while the box is memory-tight, then
          flip to `nim` with no code change.

Backend selection (env RECAST_IMAGEGEN_BACKEND, or the `backend=` argument):
  auto    (default) use the local NIM if it answers /v1/health/ready, else
          hosted if NVIDIA_API_KEY is set, else raise.
  nim     force local NIM   (RECAST_IMAGEGEN_NIM_URL, default http://127.0.0.1:8610)
  hosted  force build.nvidia.com

Modes:
  base    text -> image
  depth   text + reference photo -> image that keeps the photo's spatial layout
  canny   text + reference photo -> image that keeps the photo's edges/structure
  (depth/canny are the interesting ones for Recast: photograph the real
   lobby / floorplate, then render what it becomes as housing, a clinic, a
   maker space, ... while keeping the columns, window walls and proportions.
   The NIM container must be started with the matching NIM_MODEL_VARIANT.)

Library:
  from imagegen import generate, recast_prompt
  r = generate(recast_prompt("Lake Union Building", "vacant office floor",
                             "mixed-income apartments"))
  open("out.png", "wb").write(r.image_bytes)

CLI:
  python3 imagegen.py "a bright co-working lobby in a converted 1980s office tower"
  python3 imagegen.py --mode depth --image lobby.jpg "same lobby converted to a public library"
  python3 imagegen.py --check              # which backends are reachable
  python3 imagegen.py --recast "Lake Union Building" "vacant office floor" "apartments"

Stdlib only (urllib/json/base64). Pillow is optional -- if present, reference
photos are converted to PNG and downscaled for the hosted backend's inline
size limit; without it, pass PNGs.
"""
import os
import io
import sys
import json
import time
import base64
import argparse
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from typing import Optional

DEFAULT_NIM_URL = os.environ.get("RECAST_IMAGEGEN_NIM_URL", "http://127.0.0.1:8610")
HOSTED_URL = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev"
VALID_MODES = ("base", "depth", "canny")
# FLUX.1-dev NIM accepts these edge lengths (multiples of 64 in 768..1344).
VALID_SIZES = (768, 832, 896, 960, 1024, 1088, 1152, 1216, 1280, 1344)
# build.nvidia.com rejects inline base64 images above roughly this size;
# bigger inputs need the NVCF asset-upload flow (not implemented here).
HOSTED_INLINE_IMAGE_LIMIT = 170_000

TIMEOUT_S = 300


@dataclass
class ImageResult:
    image_bytes: bytes
    mime: str
    seed: int
    finish_reason: str
    backend: str
    elapsed_s: float
    prompt: str
    mode: str
    width: int
    height: int
    steps: int
    cfg_scale: float

    @property
    def ext(self) -> str:
        return {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(self.mime, "bin")

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("image_bytes")
        d["image_b64"] = base64.b64encode(self.image_bytes).decode("ascii")
        d["ext"] = self.ext
        return d


class ImageGenError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Prompt helper for the Recast story
# --------------------------------------------------------------------------- #

def recast_prompt(building: str, current_use: str, proposed_use: str,
                  extras: str = "", keep_structure: bool = True) -> str:
    """Build a consistent 'Alternative Future' rendering prompt.

    Kept deliberately plain: the judge should read the image as a proposal,
    not a marketing render. Pair with mode=depth/canny + a real photo of the
    space when you have one; with mode=base this is a concept image only.
    """
    parts = [
        "Photorealistic architectural visualization, Seattle, overcast daylight.",
        f"Interior of {building}, currently {current_use}, "
        f"re-imagined as {proposed_use}.",
    ]
    if keep_structure:
        parts.append("Keep the existing structure: columns, floor plate, ceiling height, "
                     "window walls and views. Adaptive reuse, not new construction.")
    parts.append("Realistic materials, a few people using the space, wide angle, "
                 "eye level, 35mm lens, no text, no watermark.")
    if extras:
        parts.append(extras.strip())
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _sniff_mime(b: bytes) -> str:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _snap_size(v: int, name: str) -> int:
    v = int(v)
    if v in VALID_SIZES:
        return v
    snapped = min(VALID_SIZES, key=lambda s: abs(s - v))
    sys.stderr.write(f"[imagegen] {name}={v} not allowed; using {snapped} "
                     f"(allowed: {VALID_SIZES[0]}..{VALID_SIZES[-1]} step 64)\n")
    return snapped


def _load_reference_image(image, for_hosted: bool) -> str:
    """Return a data URL for the depth/canny reference image.

    `image` may be a path, raw bytes, or an existing data: URL / base64 string.
    """
    if isinstance(image, str) and image.startswith("data:image/"):
        return image
    if isinstance(image, (bytes, bytearray)):
        raw = bytes(image)
    elif isinstance(image, str) and os.path.exists(image):
        with open(image, "rb") as f:
            raw = f.read()
    elif isinstance(image, str):
        # assume bare base64
        try:
            raw = base64.b64decode(image, validate=True)
        except Exception:
            raise ImageGenError(f"image is not a path, bytes, data URL or base64: {image[:40]!r}")
    else:
        raise ImageGenError("unsupported image argument type")

    mime = _sniff_mime(raw)
    try:
        from PIL import Image  # optional
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        limit = HOSTED_INLINE_IMAGE_LIMIT if for_hosted else None
        # NIM wants PNG; hosted wants PNG *and* small. Downscale until it fits.
        while True:
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            raw = buf.getvalue()
            if limit is None or len(raw) <= limit or max(im.size) <= 256:
                break
            im = im.resize((int(im.width * 0.8), int(im.height * 0.8)))
        mime = "image/png"
    except ImportError:
        if mime != "image/png":
            sys.stderr.write("[imagegen] warning: reference image is not PNG and Pillow "
                             "is not installed; sending as-is. Prefer PNG.\n")
        if for_hosted and len(raw) > HOSTED_INLINE_IMAGE_LIMIT:
            sys.stderr.write(f"[imagegen] warning: image is {len(raw)//1024} KB; the hosted "
                             f"endpoint may reject inline images over ~{HOSTED_INLINE_IMAGE_LIMIT//1024} KB\n")
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _post_json(url: str, payload: dict, headers: dict, timeout: float = TIMEOUT_S):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:2000]
        except Exception:
            detail = ""
        return e.code, {"error": detail}
    except urllib.error.URLError as e:
        raise ImageGenError(f"cannot reach {url}: {e.reason}")


# --------------------------------------------------------------------------- #
# Backend availability
# --------------------------------------------------------------------------- #

def nim_ready(nim_url: str = DEFAULT_NIM_URL, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(nim_url.rstrip("/") + "/v1/health/ready", timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def hosted_configured() -> bool:
    return bool(os.environ.get("NVIDIA_API_KEY"))


def resolve_backend(backend: Optional[str] = None, nim_url: str = DEFAULT_NIM_URL) -> str:
    backend = (backend or os.environ.get("RECAST_IMAGEGEN_BACKEND") or "auto").lower()
    if backend == "auto":
        if nim_ready(nim_url):
            return "nim"
        if hosted_configured():
            return "hosted"
        raise ImageGenError(
            f"no image backend available: NIM at {nim_url} is not ready "
            "(start it with services/image-gen/run-nim.sh on the Spark) and "
            "NVIDIA_API_KEY is not set for the hosted fallback")
    if backend not in ("nim", "hosted"):
        raise ImageGenError(f"unknown backend {backend!r} (nim | hosted | auto)")
    return backend


def check(nim_url: str = DEFAULT_NIM_URL) -> dict:
    return {
        "nim": {"url": nim_url, "ready": nim_ready(nim_url)},
        "hosted": {"url": HOSTED_URL, "configured": hosted_configured()},
        "default_backend": os.environ.get("RECAST_IMAGEGEN_BACKEND", "auto"),
    }


# --------------------------------------------------------------------------- #
# Generate
# --------------------------------------------------------------------------- #

def generate(prompt: str,
             mode: str = "base",
             image=None,
             width: int = 1024,
             height: int = 1024,
             steps: int = 30,
             cfg_scale: Optional[float] = None,
             seed: int = 0,
             backend: Optional[str] = None,
             nim_url: str = DEFAULT_NIM_URL) -> ImageResult:
    """Generate one image. Returns ImageResult (bytes + metadata).

    steps: 5..100 (hosted enforces this; 30 is a good speed/quality point on Spark)
    cfg_scale: 1 < x <= 9 on hosted. Defaults: 3.5 for base, 7.0 for depth/canny.
    seed: 0 is fine; keep it fixed for reproducible demo renders.
    """
    if not prompt or not prompt.strip():
        raise ImageGenError("prompt is empty")
    mode = (mode or "base").lower()
    if mode not in VALID_MODES:
        raise ImageGenError(f"mode must be one of {VALID_MODES}")
    if mode != "base" and image is None:
        raise ImageGenError(f"mode={mode} needs a reference image")
    if cfg_scale is None:
        cfg_scale = 3.5 if mode == "base" else 7.0
    steps = max(5, min(100, int(steps)))
    width, height = _snap_size(width, "width"), _snap_size(height, "height")
    backend = resolve_backend(backend, nim_url)

    payload = {
        "prompt": prompt.strip(),
        "mode": mode,
        "width": width,
        "height": height,
        "cfg_scale": float(cfg_scale),
        "seed": int(seed),
        "steps": steps,
    }
    if image is not None:
        payload["image"] = _load_reference_image(image, for_hosted=(backend == "hosted"))

    if backend == "hosted":
        url = HOSTED_URL
        headers = {"Authorization": "Bearer " + os.environ["NVIDIA_API_KEY"]}
    else:
        url = nim_url.rstrip("/") + "/v1/infer"
        headers = {}

    t0 = time.time()
    status, data = _post_json(url, payload, headers)
    if status == 422 and backend == "nim":
        # Older NIM builds (1.1.x) use the Stability-style field name.
        alt = dict(payload)
        alt["text_prompts"] = [{"text": alt.pop("prompt")}]
        status, data = _post_json(url, alt, headers)
    elapsed = time.time() - t0

    if status < 200 or status >= 300:
        raise ImageGenError(f"{backend} returned HTTP {status}: {data.get('error') or data}")
    arts = data.get("artifacts") or []
    if not arts or not arts[0].get("base64"):
        raise ImageGenError(f"{backend} returned no image: {json.dumps(data)[:500]}")
    art = arts[0]
    finish = art.get("finishReason") or art.get("finish_reason") or "UNKNOWN"
    if finish == "CONTENT_FILTERED":
        raise ImageGenError("image was blocked by the content filter; rephrase the prompt")
    img = base64.b64decode(art["base64"])
    return ImageResult(
        image_bytes=img, mime=_sniff_mime(img), seed=int(art.get("seed", seed)),
        finish_reason=finish, backend=backend, elapsed_s=round(elapsed, 2),
        prompt=payload["prompt"], mode=mode, width=width, height=height,
        steps=steps, cfg_scale=float(cfg_scale),
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("prompt", nargs="?", help="text prompt (or use --recast)")
    ap.add_argument("--recast", nargs=3, metavar=("BUILDING", "CURRENT_USE", "PROPOSED_USE"),
                    help="build the prompt with recast_prompt()")
    ap.add_argument("--mode", default="base", choices=VALID_MODES)
    ap.add_argument("--image", help="reference photo for depth/canny")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--cfg", type=float, default=None, dest="cfg_scale")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--backend", default=None, help="nim | hosted | auto (default: env or auto)")
    ap.add_argument("--nim-url", default=DEFAULT_NIM_URL)
    ap.add_argument("--out", default=None, help="output file (default: out/<timestamp>.<ext>)")
    ap.add_argument("--check", action="store_true", help="report backend availability and exit")
    ap.add_argument("--print-prompt", action="store_true", help="print the final prompt and exit")
    a = ap.parse_args(argv)

    if a.check:
        print(json.dumps(check(a.nim_url), indent=2))
        return 0

    prompt = a.prompt
    if a.recast:
        prompt = recast_prompt(*a.recast, extras=a.prompt or "")
    if not prompt:
        ap.error("give a prompt or --recast BUILDING CURRENT_USE PROPOSED_USE")
    if a.print_prompt:
        print(prompt)
        return 0

    try:
        r = generate(prompt, mode=a.mode, image=a.image, width=a.width, height=a.height,
                     steps=a.steps, cfg_scale=a.cfg_scale, seed=a.seed,
                     backend=a.backend, nim_url=a.nim_url)
    except ImageGenError as e:
        sys.stderr.write(f"error: {e}\n")
        return 1

    out = a.out
    if not out:
        os.makedirs("out", exist_ok=True)
        out = os.path.join("out", time.strftime("%Y%m%d-%H%M%S") + f"-{r.mode}.{r.ext}")
    with open(out, "wb") as f:
        f.write(r.image_bytes)
    print(f"wrote {out}  ({r.backend}, {r.mode}, {r.width}x{r.height}, "
          f"{r.steps} steps, seed {r.seed}, {r.elapsed_s}s)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
