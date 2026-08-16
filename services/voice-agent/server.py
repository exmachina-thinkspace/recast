"""Voice-agent server -- ties together the pieces already proven working
this session: Whisper STT (local, CPU, no GPU memory cost), the grounded
BHI explanation pattern (Qwen via vLLM), and serves the browser page that
does text-to-speech client-side (browser SpeechSynthesis -- runs locally on
the user's machine, not a cloud call, so it doesn't touch the box's memory
budget or the "nothing leaves the box" privacy story).

Everything is same-origin from the browser's perspective (page + API both
served from this one process), which avoids CORS entirely instead of
fighting it.

Endpoints:
  GET  /                 -> the voice UI (index.html, same directory)
  POST /transcribe        -> audio bytes in, {"text": ...} out (Whisper)
  POST /chat               -> {"message": ...} in, grounded BHI answer out (Qwen)
  GET  /health

Usage:
  python3 server.py --port 8601
"""
import os
import sys
import json
import time
import argparse
import tempfile
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_tools

HERE = os.path.dirname(os.path.abspath(__file__))
PLANS = os.path.expanduser("~/plans")
VLLM_API = "http://127.0.0.1:8000/v1/chat/completions"
VLLM_MODEL = "nvidia/Qwen3.6-35B-A3B-NVFP4"

WHISPER_MODEL_SIZE = "small"
WHISPER_CACHE = "/media/acer01/SB-XTM5/models/whisper"
COSMOS_API = "http://127.0.0.1:30082/v1/models"
AUDIO_MIN_BYTES = 1024

SYSTEM_PROMPT = (
    "You are the Build Vitals voice assistant, covering the hero building "
    "(Lake Union Building, 1700 Westlake Ave N) and 124 other Seattle "
    "downtown/South Lake Union buildings. You have tools to look up real "
    "BHI scores, crime data, permit history, and business tenancy -- use "
    "them instead of guessing, and call get_bhi first if you don't already "
    "know which building the user means. Never invent a number, source, or "
    "fact that isn't in a tool result. If a tool returns insufficient "
    "evidence, say so plainly. Use the approved vocabulary: potentially "
    "underused not vacant, observed low activity not empty, insufficient "
    "evidence not a guessed low score. Keep answers to 2-3 sentences -- "
    "this is a spoken voice response, not a written report."
)
MAX_TOOL_ROUNDS = 4

_whisper_model = None


def _endpoint_ready(url, timeout=1.5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def service_health():
    """Report wrapper and model dependencies separately.

    `ok` intentionally means this HTTP wrapper is alive. Consumers should use
    the capability flags instead of showing AGENT ONLINE from `ok` alone.
    """
    qwen_ready = _endpoint_ready("http://127.0.0.1:8000/v1/models")
    cosmos_ready = _endpoint_ready(COSMOS_API)
    whisper_cached = os.path.isdir(WHISPER_CACHE)
    return {
        "ok": True,
        "agent_ready": qwen_ready,
        "transcription_ready": whisper_cached,
        "vision_ready": cosmos_ready,
        "models": {
            "agent": VLLM_MODEL,
            "transcription": f"faster-whisper/{WHISPER_MODEL_SIZE}",
            "vision": "nvidia/cosmos3-nano-reasoner",
        },
    }


def _audio_suffix(content_type):
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    return {
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/aiff": ".aiff",
        "audio/x-aiff": ".aiff",
    }.get(mime, ".webm")


def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu",
                                        compute_type="int8", download_root=WHISPER_CACHE)
    return _whisper_model


def _vllm_call(messages, tools=None):
    payload = {
        "model": VLLM_MODEL,
        "messages": messages,
        "max_tokens": 500,
        "temperature": 0.2,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(VLLM_API, data=json.dumps(payload).encode(),
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def run_agent_turn(user_message):
    """Real tool-calling loop: the model can call get_bhi/search_crime/
    search_permits/search_businesses (see agent_tools.py), see the real
    results, and either call more tools or give a final answer. Caps at
    MAX_TOOL_ROUNDS so a confused model can't loop forever. Returns
    (final_text, trace) where trace lists which tools were actually called,
    for debugging/demo transparency."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    trace = []
    for _ in range(MAX_TOOL_ROUNDS):
        resp = _vllm_call(messages, tools=agent_tools.TOOL_SCHEMAS)
        msg = resp["choices"][0]["message"]
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return msg.get("content") or "(no response)", trace
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                          "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                args = {}
            result = agent_tools.call_tool(fn, args)
            trace.append({"tool": fn, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                              "content": json.dumps(result)})
    # ran out of rounds -- force a final answer from what we have
    resp = _vllm_call(messages)
    return resp["choices"][0]["message"].get("content") or "(no response)", trace


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        # Additive: lets the new combined frontend (served from a different
        # port) call this API. Does not change any existing endpoint
        # behavior, response body, or status code.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        # CORS preflight support, additive -- browsers send this before a
        # cross-origin POST with a JSON content-type.
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, service_health())
        elif self.path == "/" or self.path == "/index.html":
            index = os.path.join(HERE, "index.html")
            with open(index, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        if self.path == "/transcribe":
            if len(raw) < AUDIO_MIN_BYTES:
                self._send_json(400, {
                    "error": "recording was empty or too short; hold the microphone button for at least one second"
                })
                return
            t0 = time.time()
            suffix = _audio_suffix(self.headers.get("Content-Type"))
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                tf.write(raw)
                tmp_path = tf.name
            try:
                model = get_whisper()
                segments, info = model.transcribe(tmp_path)
                text = " ".join(s.text.strip() for s in segments)
                self._send_json(200, {"text": text.strip(),
                                        "language": info.language,
                                        "elapsed_s": round(time.time() - t0, 2)})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            finally:
                os.unlink(tmp_path)

        elif self.path == "/chat":
            try:
                body = json.loads(raw)
                message = body.get("message", "")
                answer, trace = run_agent_turn(message)
                self._send_json(200, {"answer": answer, "tool_trace": trace})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif self.path == "/analyze-image":
            # New, additive endpoint (#2) -- direct image upload -> Cosmos
            # description, outside the chat/tool loop. Existing /chat and
            # /transcribe behavior is untouched.
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                tf.write(raw)
                tmp_path = tf.name
            try:
                result = agent_tools.vision_tools.describe_uploaded_image(tmp_path)
                self._send_json(200, result)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            finally:
                os.unlink(tmp_path)
        else:
            self._send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[voice-agent] " + (fmt % args) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8601)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("[voice-agent] listening on http://0.0.0.0:%d" % args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
