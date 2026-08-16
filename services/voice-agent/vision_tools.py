"""New, additive vision tools for the voice agent -- image-input handling
(#2) and the "what could this become" room-reuse tool (#3). Does not
modify content_authenticity.py, build_vitals_v2.py, or recast_view.py;
reuses their proven Cosmos-call pattern standalone and reads their output
files rather than importing/altering their logic, so none of those
existing workflows are touched.

Cosmos (nvidia-cosmos3-reasoner) is already resident on the box for VSS --
calling it adds no new memory load, unlike an image-generation model.
"""
import os
import json
import base64
import urllib.request

PLANS = os.path.expanduser("~/plans")
ARLO_FRAMES = os.path.expanduser("~/arlo-frames")
COSMOS_API = os.environ.get("RECAST_COSMOS_API", "http://127.0.0.1:30082/v1/chat/completions")
COSMOS_MODEL = "nvidia/cosmos3-nano-reasoner"

# Known-good captured frames from the live camera pipeline (services/vision-bridge
# session). Not a live RTSP pull -- that's still blocked on credentials not
# present in this non-interactive SSH session, same limitation noted earlier.
KNOWN_FRAMES = {
    "lobby": os.path.join(ARLO_FRAMES, "lobby.jpg"),
    "common_area": os.path.join(ARLO_FRAMES, "common.jpg"),
    "sw_hallway": os.path.join(ARLO_FRAMES, "swhall.jpg"),
}


def _cosmos_call(image_path, prompt, max_tokens=250, timeout=60):
    if not os.path.exists(image_path):
        return None
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception:
        return None
    payload = json.dumps({
        "model": COSMOS_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,%s" % b64}},
        ]}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(COSMOS_API, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.load(r)
        return resp["choices"][0]["message"].get("content")
    except Exception:
        return None


def list_camera_frames():
    """What's actually available -- never claims a live feed that isn't there."""
    return {name: os.path.exists(path) for name, path in KNOWN_FRAMES.items()}


def describe_camera_frame(zone, question=None):
    """Cosmos description of a known captured camera frame (not a live
    pull). Returns None if the frame or the Cosmos call isn't available --
    caller must handle that, never fabricate a description."""
    path = KNOWN_FRAMES.get(zone)
    if not path or not os.path.exists(path):
        return {"error": "no captured frame for zone '%s'. Available: %s" %
                          (zone, [k for k, v in list_camera_frames().items() if v])}
    prompt = question or "Describe this space: layout, condition, and any visible activity or people."
    text = _cosmos_call(path, prompt)
    if text is None:
        return {"error": "Cosmos vision call failed or unavailable"}
    return {"zone": zone, "source": "captured camera frame (not live)", "description": text}


def describe_uploaded_image(image_path, question=None):
    """Same as describe_camera_frame but for a freshly uploaded file."""
    if not os.path.exists(image_path):
        return {"error": "uploaded image not found"}
    prompt = question or "Describe this space: layout, condition, and any visible activity or people."
    text = _cosmos_call(image_path, prompt)
    if text is None:
        return {"error": "Cosmos vision call failed or unavailable"}
    return {"source": "uploaded image", "description": text}


def whats_next(target_use=None):
    """Reads recast_view.py's already-generated output (does not
    re-run or modify that script) and asks Cosmos to describe what the
    top candidate room(s) would need for a given reuse, grounded in the
    real room-size heuristic result -- not a free-form guess."""
    summary_path = os.path.join(PLANS, "recast_summary.json")
    if not os.path.exists(summary_path):
        return {"error": "no recast_summary.json -- run recast_view.py first"}
    summary = json.load(open(summary_path))

    counts = {}
    for level in summary.get("levels", {}).values():
        for use, n in level.get("by_use", {}).items():
            counts[use] = counts.get(use, 0) + n

    if not counts:
        return {"error": "recast summary has no room data"}

    best_use = target_use if target_use in counts else max(counts, key=counts.get)

    floorplan_img = os.path.join(PLANS, "level1_floorplan.png")
    description = None
    if os.path.exists(floorplan_img):
        prompt = ("This building has %d rooms flagged as best-fit for '%s' by a room-size "
                   "heuristic (not a code review). In 2-3 sentences, describe what a real "
                   "conversion to that use would realistically require beyond size -- think "
                   "plumbing, natural light, egress -- without claiming the conversion is "
                   "approved or feasible." % (counts.get(best_use, 0), best_use))
        description = _cosmos_call(floorplan_img, prompt, max_tokens=200)

    return {
        "tier": "T3",
        "note": "deterministic room-size heuristic (recast_view.py), not a code review",
        "room_counts_by_use": counts,
        "highlighted_use": best_use,
        "cosmos_description": description or "(Cosmos unavailable -- showing room-count data only)",
    }
