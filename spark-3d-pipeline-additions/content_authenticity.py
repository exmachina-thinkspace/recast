"""Best-effort authenticity check for owner/user-submitted photos and video.

EXPERIMENTAL. This is not a certified deepfake/AI-image detector -- there is
no such model already on the Spark box, and training one is out of scope for
this pipeline. What this module actually does:

  1. Metadata check (weak signal): real camera photos normally carry EXIF
     data (camera make/model, GPS, timestamp). Its absence is common in
     AI-generated or screenshotted images but proves nothing by itself --
     easy to strip real EXIF too. T3 at best.
  2. VLM opinion (weak signal): asks the local vision-language model whether
     the image looks synthetic. LLM "does this look AI-generated" judgments
     are known to be unreliable and should not gate anything on their own.
  3. C2PA / content-credentials check (strongest signal IF present): if the
     file carries a Content Credentials manifest (C2PA), that's a real
     cryptographic provenance signal. Most consumer phone photos today do
     NOT carry one, so absence is not evidence of anything.

None of these alone should silently reject a submission. The intended use
is: run all available checks, attach the result to the submission, and route
anything flagged below a confidence threshold to human review before it
affects owner_media or user_reviews_metadata scoring in build_vitals_v2.py.

What this module deliberately does NOT do: web search / reverse-image search
to corroborate who posted what. That needs a real search API and a decision
about credential handling and scope that hasn't been made yet -- stubbed as
NotImplementedError so it fails loudly instead of pretending to work.
"""
import os
import json
import base64
import urllib.request
import urllib.error

VLM_API = "http://127.0.0.1:8000/v1/chat/completions"
VLM_MODEL = "nvidia/Qwen3.6-35B-A3B-NVFP4"

COSMOS_API = "http://127.0.0.1:30082/v1/chat/completions"
COSMOS_MODEL = "nvidia/cosmos3-nano-reasoner"


def check_exif(image_path):
    """Returns {'has_camera_exif': bool, 'note': str} or None if unreadable."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
    except ImportError:
        return {"has_camera_exif": None, "note": "PIL not installed, EXIF check skipped"}
    try:
        img = Image.open(image_path)
        exif = img._getexif()
        if not exif:
            return {"has_camera_exif": False, "note": "no EXIF block present"}
        tags = {TAGS.get(k, k): v for k, v in exif.items()}
        has_cam = "Make" in tags or "Model" in tags
        return {"has_camera_exif": has_cam,
                "note": "camera make/model present" if has_cam else "EXIF present but no camera make/model"}
    except Exception as e:
        return {"has_camera_exif": None, "note": "could not read EXIF: %s" % e}


def check_c2pa(image_path):
    """Content Credentials (C2PA) check. Requires the `c2patool` binary,
    which is not installed on the Spark by default -- returns unavailable
    rather than silently skipping, so the caller knows this check didn't run.
    """
    import shutil
    if not shutil.which("c2patool"):
        return {"available": False, "note": "c2patool not installed -- provenance check not run"}
    import subprocess
    try:
        out = subprocess.run(["c2patool", image_path], capture_output=True, text=True, timeout=15)
        has_manifest = out.returncode == 0 and '"manifests"' in out.stdout
        return {"available": True, "has_manifest": has_manifest,
                "note": "C2PA manifest found" if has_manifest else "no C2PA manifest (common for phone photos, proves nothing alone)"}
    except Exception as e:
        return {"available": True, "has_manifest": None, "note": "c2patool failed: %s" % e}


def check_vlm_opinion(image_path, timeout=60):
    """Ask the local VLM whether the image looks AI-generated. Requires the
    serving model to accept image content blocks -- the Qwen3.6 instance
    this pipeline has been using for text explanation may or may not have
    vision enabled (SPARK-HANDOFF.md notes a separate vision-capable GGUF
    twin exists but was not confirmed as what's being served). This
    function fails loudly rather than pretending to succeed.
    """
    if not os.path.exists(image_path):
        return {"ran": False, "note": "file not found: %s" % image_path}
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception as e:
        return {"ran": False, "note": "could not read file: %s" % e}

    payload = json.dumps({
        "model": VLM_MODEL,
        "messages": [
            {"role": "system", "content": (
                "You assess whether a submitted building-condition photo looks "
                "AI-generated or manipulated. Answer with a confidence 0-1 that "
                "it is synthetic, and one sentence why. This is a weak signal, "
                "not a verdict -- say so if you are unsure."
            )},
            {"role": "user", "content": [
                {"type": "text", "text": "Does this image look AI-generated or manipulated?"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,%s" % b64}},
            ]},
        ],
        "max_tokens": 200,
        "temperature": 0.1,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()

    req = urllib.request.Request(VLM_API, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.load(r)
        content = resp["choices"][0]["message"].get("content")
        if not content:
            return {"ran": False, "note": "endpoint returned no content -- vision likely not enabled on this model"}
        return {"ran": True, "raw_response": content,
                "note": "unverified VLM opinion, do not treat as ground truth"}
    except urllib.error.HTTPError as e:
        return {"ran": False, "note": "HTTP %d -- endpoint may not accept image inputs" % e.code}
    except Exception as e:
        return {"ran": False, "note": "request failed: %s" % e}


def _vlm_image_opinion(image_path, api_url, model, prompt, timeout=60):
    """Shared plumbing for any OpenAI-compatible vision endpoint. Returns
    {'ran': bool, 'raw_response'|'note': str}."""
    if not os.path.exists(image_path):
        return {"ran": False, "note": "file not found: %s" % image_path}
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception as e:
        return {"ran": False, "note": "could not read file: %s" % e}

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,%s" % b64}},
            ]},
        ],
        "max_tokens": 250,
        "temperature": 0.1,
    }).encode()

    req = urllib.request.Request(api_url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.load(r)
        content = resp["choices"][0]["message"].get("content")
        if not content:
            return {"ran": False, "note": "endpoint returned no content"}
        return {"ran": True, "raw_response": content}
    except urllib.error.HTTPError as e:
        return {"ran": False, "note": "HTTP %d -- endpoint may not accept image inputs" % e.code}
    except Exception as e:
        return {"ran": False, "note": "request failed: %s" % e}


def check_cosmos_opinion(image_path, timeout=60):
    """Second, independent vision-model opinion using NVIDIA's Cosmos
    reasoner (nvidia-cosmos3-reasoner, already running on the box for VSS).
    Agreement between two differently-trained models is a somewhat stronger
    signal than either alone -- still not a verdict, still routes to human
    review, but disagreement between Qwen and Cosmos is itself useful
    information (flag for review rather than average away).
    """
    result = _vlm_image_opinion(
        image_path, COSMOS_API, COSMOS_MODEL,
        "Does this image look AI-generated, staged, or manipulated? Give a "
        "confidence 0-1 that it is synthetic and one sentence why. This is a "
        "weak signal, not a verdict -- say so if unsure.",
        timeout=timeout,
    )
    result["note"] = "unverified Cosmos opinion, do not treat as ground truth"
    return result


def check_reverse_search(image_path):
    """Corroborate submitted content via web/reverse-image search.
    NOT IMPLEMENTED. Needs: a search API, a decision on credential handling
    (this pipeline does not accept or store API tokens from chat -- see
    project convention), and a scoping decision from the team on what
    'corroborated' means. Fails loudly rather than faking a result.
    """
    raise NotImplementedError(
        "reverse-image/web corroboration is not built. Requires a search API "
        "and a team decision on scope -- see docs/model-evaluation.md open items."
    )


def check(image_path, run_vlm=True):
    """Run all available checks and return a combined, honest report. Does
    NOT return a single pass/fail verdict on purpose -- the caller (or a
    human reviewer) decides what to do with a low-confidence result.

    Two independent vision models (Qwen, Cosmos) both weigh in when
    run_vlm=True. They disagreeing is itself signal worth surfacing, not
    something to silently average away.
    """
    result = {
        "image_path": image_path,
        "exif": check_exif(image_path),
        "c2pa": check_c2pa(image_path),
        "qwen_opinion": check_vlm_opinion(image_path) if run_vlm else {"ran": False, "note": "skipped"},
        "cosmos_opinion": check_cosmos_opinion(image_path) if run_vlm else {"ran": False, "note": "skipped"},
        "verdict": "NOT DETERMINED -- route to human review",
        "warning": "No component of this check is a certified authenticity verdict. "
                   "Treat all signals as weak until a real detector is integrated.",
    }
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python3 content_authenticity.py <image_path>")
        sys.exit(1)
    print(json.dumps(check(sys.argv[1]), indent=2))
