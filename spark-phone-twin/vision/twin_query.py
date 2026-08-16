#!/usr/bin/env python3
"""
twin_query.py -- natural-language questions over the digital twin's time
dimension: occupancy_log.py (history) + scenegraph.json (current room
labels/types). Phrasing of the final answer is done by Cosmos Reason 3 Nano
(nvidia-cosmos3-reasoner, :30082, OpenAI-compatible) -- the same endpoint
room_caption.py already uses -- so the numbers are computed deterministically
in Python and only the sentence is LLM-written.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import occupancy_log  # noqa: E402

PLANS = os.path.expanduser("~/plans")
SCENEGRAPH = os.path.join(PLANS, "scenegraph.json")
COSMOS_URL = "http://127.0.0.1:30082/v1/chat/completions"
MODEL = "nvidia/cosmos3-nano-reasoner"


def _room_types():
    try:
        sg = json.load(open(SCENEGRAPH))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    out = {}
    for lvl in sg.get("levels", []):
        for r in lvl.get("rooms", []):
            out[r["room_id"]] = r.get("inferred_type") or "unclassified space"
    return out


def build_context(hours: float = 24.0) -> dict:
    summary = occupancy_log.summarise(hours)
    types = _room_types()
    rooms = []
    for rid, r in summary["rooms"].items():
        rooms.append({
            "room_id": rid,
            "level": r["level"],
            "type": types.get(rid, "unclassified space"),
            "peak_count": r["peak_count"],
            "occupied_minutes": r["occupied_minutes"],
            "coverage_minutes": r["coverage_minutes"],
            "utilisation_pct": round(r["utilisation_fraction"] * 100, 1),
            "was_occupied": r["peak_count"] > 0,
        })
    return {
        "hours_covered": summary["hours_covered"],
        "total_samples": summary["total_samples"],
        "rooms": rooms,
    }


def ask(question: str, hours: float = 24.0, timeout: float = 30.0) -> dict:
    """Answer `question` using the occupancy log + scenegraph as ground
    truth data, phrased by Cosmos Reason. Returns {"context", "answer"}."""
    ctx = build_context(hours)
    prompt = (
        "You are answering a question about a building's occupancy history. "
        "Use ONLY the JSON data below -- do not invent rooms or numbers not "
        "present in it. If the data shows zero occupied rooms, say so "
        "plainly; do not guess. Be concise (2-4 sentences).\n\n"
        "DATA:\n" + json.dumps(ctx) + "\n\nQUESTION: " + question
    )
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        COSMOS_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    answer = body["choices"][0]["message"]["content"]
    return {"question": question, "context": ctx, "answer": answer,
            "usage": body.get("usage")}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?", default="Which rooms were occupied today?")
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args()
    result = ask(a.question, a.hours)
    print("Q:", result["question"])
    print("A:", result["answer"])
