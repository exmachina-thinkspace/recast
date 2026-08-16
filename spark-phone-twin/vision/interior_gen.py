"""
interior_gen.py - Structure-preserving interior re-render using Stable Diffusion 1.5
+ ControlNet (depth conditioning), for the DGX Spark digital-twin pipeline.

Use case: take a real room (from a phone-camera reconstruction / floor-plan digital
twin) and re-render it as a different room type (dentist office, coworking space,
etc.) while KEEPING the room's real geometry -- walls, proportions, window/door
positions. Plain text-to-image is not sufficient for this; ControlNet depth
conditioning locks the diffusion process to the real room's structure.

Model choice (fits the DGX Spark's memory budget):
  - Base: stable-diffusion-v1-5/stable-diffusion-v1-5  (~4 GB fp16)
  - ControlNet: lllyasviel/control_v11f1p_sd15_depth    (~1.4 GB fp16)
  SD1.5 was chosen over SDXL because it is roughly 3x smaller in memory and has the
  widest selection of mature ControlNet variants, at the cost of some image quality/
  resolution versus SDXL. Given the tight, no-swap-cushion memory budget on this
  machine, fitting reliably was prioritized over maximum fidelity.

Accepts EITHER:
  - a precomputed depth map (.npy, float32 metric depth, e.g. from the site's own
    3D reconstruction pipeline), or
  - an ordinary photo (.jpg/.png), from which a depth map is derived automatically
    using the Depth-Anything-V2 model (already cached on this box).

Usage:
    from interior_gen import generate
    generate("/home/acer01/arlo-frames/lobby.jpg", "dentist office", "/home/acer01/plans/gen_dentist.png")
    generate("/home/acer01/arlo-frames/lobby.jpg", "coworking space", "/home/acer01/plans/gen_cowork.png")

Or from the CLI:
    python3 interior_gen.py <structure_img_path> <preset> <out_path>
"""

import os
import sys
import time

import numpy as np
import torch
from PIL import Image

MODEL_ID = "stable-diffusion-v1-5/stable-diffusion-v1-5"
CONTROLNET_ID = "lllyasviel/control_v11f1p_sd15_depth"
DEPTH_MODEL_ID = "depth-anything/Depth-Anything-V2-Metric-Indoor-Base-hf"

TARGET_SIZE = (512, 512)  # SD1.5 native res; keep small for memory/time safety

# Shared negative-prompt baseline. Every preset extends this with terms that
# push away from the OTHER presets' furniture/fixtures, which matters more
# than generic quality terms once ControlNet is locking the geometry: without
# it, SD1.5 tends to leave a stray reception desk or exam table behind from
# the model's overall interior-photo prior.
NEGATIVE_PROMPT = (
    "blurry, low quality, distorted architecture, warped walls, extra rooms, "
    "watermark, text, people, cartoon, deformed, low resolution"
)


def _preset(prompt, negative_extra=""):
    return {
        "prompt": prompt,
        "negative_prompt": NEGATIVE_PROMPT + (", " + negative_extra if negative_extra else ""),
    }


# Presets researched 2026-08-15 against current US commercial-real-estate
# adaptive-reuse activity (see snapshot_gen.py / project report for sources).
# Office-to-residential and office-to-medical are the two largest, most
# commercially proven conversion categories nationally right now; the rest
# round out the mix of what actually gets built into underused office/retail
# space (coworking, food & beverage, fitness, childcare, self-storage, and
# life-science retrofits all have real, cited 2025-2026 activity).
PRESETS = {
    "dentist office": _preset(
        "modern dental practice interior, clean, bright, professional, dental "
        "chair with overhead light, sink, sterilization cabinetry, soft white "
        "and blue tones, reception counter, large windows, natural light, "
        "photorealistic architectural render",
        "gore, blood, surgical scene, hospital gurney, industrial warehouse "
        "look, restaurant seating",
    ),
    "medical clinic": _preset(
        "modern outpatient medical clinic interior, exam room with exam table, "
        "reception desk, waiting-area seating, clean clinical white and pale "
        "blue palette, medical signage, vinyl flooring, recessed lighting, "
        "photorealistic architectural render",
        "dental chair, gore, blood, surgical scene, residential furniture, "
        "gym equipment",
    ),
    "condo": _preset(
        "modern apartment / condo living space interior, sofa and coffee "
        "table, stylish furniture, warm wood flooring, cozy contemporary "
        "decor, small kitchenette, large windows, natural light, "
        "photorealistic architectural render",
        "office cubicles, reception desk, industrial fixtures, medical "
        "equipment, gym equipment",
    ),
    "garage": _preset(
        "clean organized garage interior, epoxy floor, tool storage, "
        "workbench, concrete walls, overhead lighting, photorealistic "
        "architectural render",
    ),
    "coworking space": _preset(
        "modern coworking space interior, open-plan shared desks, phone "
        "booths, plants, exposed beams, warm wood and industrial accents, "
        "natural light, collaborative lounge area, photorealistic "
        "architectural render",
        "residential bedroom furniture, medical equipment, dental chair, "
        "gym equipment",
    ),
    "private office suite": _preset(
        "private single-tenant office suite interior, executive desk, "
        "ergonomic chair, built-in shelving, tasteful neutral palette, small "
        "glass-partition meeting nook, quiet professional atmosphere, "
        "natural light, photorealistic architectural render",
        "open coworking bullpen, cafe counter, medical equipment, residential "
        "bed, gym equipment",
    ),
    "cafe": _preset(
        "cozy modern cafe / coffee shop interior, wooden tables, pendant "
        "lighting, espresso bar counter, plants, warm inviting atmosphere, "
        "large windows, photorealistic architectural render",
        "office cubicles, medical equipment, gym equipment, storage racks",
    ),
    "retail": _preset(
        "modern retail storefront interior, clean product displays, bright "
        "lighting, minimalist shelving, inviting layout, large windows, "
        "photorealistic architectural render",
        "office cubicles, medical equipment, restaurant seating, gym "
        "equipment",
    ),
    "fitness studio": _preset(
        "boutique fitness studio interior, rubber flooring, wall mirrors, "
        "exercise equipment neatly arranged, exposed ceiling, energetic "
        "bright lighting, open motivating layout, photorealistic "
        "architectural render",
        "office cubicles, medical exam table, restaurant seating, storage "
        "shelving",
    ),
    "childcare center": _preset(
        "licensed childcare / daycare center interior, colorful safe "
        "flooring, low child-height furniture and cubbies, reading nook, "
        "soft rounded edges, bright cheerful natural light, clean welcoming "
        "classroom, photorealistic architectural render",
        "sharp furniture edges, industrial equipment, dark moody lighting, "
        "alcohol, adult-only decor, gym equipment",
    ),
    "restaurant": _preset(
        "full-service restaurant dining room interior, warm ambient "
        "lighting, dining tables and banquette seating, open layout toward a "
        "display kitchen or bar, tasteful decor, inviting atmosphere, "
        "photorealistic architectural render",
        "office cubicles, hospital equipment, gym equipment, storage racks",
    ),
    "self storage": _preset(
        "self-storage facility interior corridor, modular metal storage unit "
        "doors, roll-up doors, polished concrete floor, bright overhead "
        "industrial lighting, clean utilitarian design, photorealistic "
        "architectural render",
        "furniture, dining tables, medical equipment, plants, decorative "
        "elements, people",
    ),
    "life science lab": _preset(
        "life-science laboratory interior, stainless steel lab benches, fume "
        "hoods, overhead utility racks, epoxy flooring, clean-room "
        "aesthetic, bright even lighting, scientific equipment, "
        "photorealistic architectural render",
        "residential furniture, restaurant seating, gore, blood, hazard "
        "spill, gym equipment",
    ),
}

_pipe = None
_depth_estimator = None


def _load_pipe():
    global _pipe
    if _pipe is not None:
        return _pipe

    from diffusers import ControlNetModel, StableDiffusionControlNetPipeline, UniPCMultistepScheduler

    t0 = time.time()
    controlnet = ControlNetModel.from_pretrained(
        CONTROLNET_ID, torch_dtype=torch.float16
    )
    pipe = StableDiffusionControlNetPipeline.from_pretrained(
        MODEL_ID,
        controlnet=controlnet,
        torch_dtype=torch.float16,
        safety_checker=None,
        requires_safety_checker=False,
    )
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config)
    pipe.enable_attention_slicing()
    pipe.enable_vae_slicing()
    pipe = pipe.to("cuda")
    print(f"[interior_gen] pipeline loaded in {time.time() - t0:.1f}s", file=sys.stderr)
    _pipe = pipe
    return _pipe


def _depth_from_npy(path):
    depth = np.load(path).astype(np.float32)
    return depth


def _depth_from_photo(path):
    """Derive a metric-ish depth map from an ordinary photo using Depth-Anything-V2."""
    global _depth_estimator
    if _depth_estimator is None:
        from transformers import pipeline as hf_pipeline
        _depth_estimator = hf_pipeline(
            "depth-estimation", model=DEPTH_MODEL_ID, device=0
        )
    img = Image.open(path).convert("RGB")
    result = _depth_estimator(img)
    depth = np.array(result["depth"], dtype=np.float32)
    return depth


def _depth_to_control_image(depth: np.ndarray, size=TARGET_SIZE) -> Image.Image:
    """Normalize a raw depth array to an 8-bit RGB PIL image ControlNet expects."""
    d = depth.copy()
    d = np.nan_to_num(d, nan=np.nanmedian(d))
    lo, hi = np.percentile(d, 1), np.percentile(d, 99)
    if hi <= lo:
        hi = lo + 1e-6
    d = np.clip((d - lo) / (hi - lo), 0, 1)
    # ControlNet depth convention: near = bright, far = dark
    d_img = (d * 255).astype(np.uint8)
    img = Image.fromarray(d_img).convert("RGB")
    img = img.resize(size, Image.BICUBIC)
    return img


def _load_control_image(structure_img_path: str) -> Image.Image:
    ext = os.path.splitext(structure_img_path)[1].lower()
    if ext == ".npy":
        depth = _depth_from_npy(structure_img_path)
    else:
        depth = _depth_from_photo(structure_img_path)
    return _depth_to_control_image(depth)


def generate(
    structure_img_path: str,
    preset: str,
    out_path: str,
    num_inference_steps: int = 20,
    guidance_scale: float = 7.5,
    controlnet_conditioning_scale: float = 1.0,
    seed: int = 42,
) -> str:
    """
    Render `structure_img_path` (a depth .npy OR an ordinary photo) restyled as
    `preset` (see PRESETS for options), preserving the room's real geometry via
    ControlNet depth conditioning. Writes PNG to `out_path`. Returns out_path.
    """
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset {preset!r}. Options: {list(PRESETS)}")

    pipe = _load_pipe()
    control_image = _load_control_image(structure_img_path)

    generator = torch.Generator(device="cuda").manual_seed(seed)
    spec = PRESETS[preset]

    t0 = time.time()
    result = pipe(
        prompt=spec["prompt"],
        negative_prompt=spec["negative_prompt"],
        image=control_image,
        num_inference_steps=num_inference_steps,
        guidance_scale=guidance_scale,
        controlnet_conditioning_scale=controlnet_conditioning_scale,
        generator=generator,
    )
    elapsed = time.time() - t0
    print(f"[interior_gen] generated {preset!r} in {elapsed:.1f}s "
          f"({num_inference_steps} steps)", file=sys.stderr)

    image = result.images[0]
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    image.save(out_path)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(f"usage: {sys.argv[0]} <structure_img_path> <preset> <out_path>")
        print(f"presets: {list(PRESETS)}")
        sys.exit(1)
    src, preset_arg, out = sys.argv[1], sys.argv[2], sys.argv[3]
    generate(src, preset_arg, out)
