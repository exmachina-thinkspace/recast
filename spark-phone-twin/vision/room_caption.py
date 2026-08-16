#!/usr/bin/env python3
"""
room_caption.py -- Layer 8 (Cosmos Reason 3 Nano) prototype.

THE GAP: the app's scenegraph (scenegraph3d.py) only knows what
YOLO11m-seg's fixed vocabulary can name -- "chair", "tv", "desk", etc.
It has no idea a room IS a dental surgery, a server closet, or a lobby;
it can't say whether a room looks occupied or vacant; it can't surface
anything not in COCO's ~80 classes (a whiteboard, a disco ball, an exit
sign, a branded banner).

THE FIX (prototyped here, not wired in): send a phone frame straight to
Cosmos Reason 3 Nano -- already running as `nvidia-cosmos3-reasoner`
(port 30082, OpenAI-compatible /v1/chat/completions, this machine's
Layer 8) -- with a prompt that asks for a small structured JSON record:
room_type, occupancy, notable_objects, description. That record is a
natural per-room attribute to attach to a scenegraph "room" node
alongside its detected object children.

No model loads locally, no GPU memory pressure added beyond what's
already resident for nvidia-cosmos3-reasoner; this script only speaks
HTTP to the existing container.

Usage:
    python3 room_caption.py path/to/frame.jpg [path/to/frame2.jpg ...]

Wiring point (left to whoever owns phone_slam.py/scenegraph3d.py): call
caption_room() once per room the first time phone_slam.place() anchors
a frame to a new room id (or on a timer/manual refresh), and store the
returned dict as an attribute on that room's scenegraph node so ROOMS/
BUILDING tabs can render "Conference Room -- vacant" instead of just a
room number.
"""
from __future__ import annotations

import argparse
import base64
import json
import urllib.request

COSMOS_URL = "http://127.0.0.1:30082/v1/chat/completions"
MODEL = "nvidia/cosmos3-nano-reasoner"

PROMPT = (
    "You are annotating a live building digital twin. Look at this phone "
    "camera frame of a room and respond with ONLY compact JSON, no markdown, "
    "no extra text, matching exactly this schema: "
    '{"room_type": string, "occupancy": "occupied"|"vacant"|"unclear", '
    '"notable_objects": [string, ...], "description": string (max 20 words)}'
)


def caption_room(image_path: str, timeout: float = 30.0) -> dict:
    """Send one phone frame to Cosmos Reason and parse its JSON verdict
    about the room. Raises on transport error; returns a dict with a
    "_raw" fallback key if the model's reply wasn't valid JSON."""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}
        ],
        "max_tokens": 200,
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        COSMOS_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())

    content = body["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"_raw": content}
    parsed["_usage"] = body.get("usage")
    parsed["_image"] = image_path
    return parsed


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+", help="one or more phone-frame JPEGs")
    args = ap.parse_args()

    for path in args.images:
        result = caption_room(path)
        print(f"=== {path} ===")
        print(json.dumps(result, indent=2))
