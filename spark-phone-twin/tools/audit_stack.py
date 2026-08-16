"""Automated stack audit: 15 layers x {installed, utilized, tested}.

Run ON the Spark:  python3 audit_stack.py
Emits a table plus audit.json. Designed to be run repeatedly — the goal is
three consecutive passes with every layer green.
"""
import os, json, subprocess, time, urllib.request, socket

HOME = os.path.expanduser("~")
R = {}


def sh(cmd, t=25):
    try:
        p = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=t)
        return (p.stdout + p.stderr).strip()
    except Exception as e:
        return "ERR:%s" % e


def http(url, t=6, data=None):
    try:
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"} if data else {})
        with urllib.request.urlopen(req, timeout=t) as r:
            return r.status, r.read(2_000_000).decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def port(p, host="127.0.0.1"):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((host, p)); return True
    except Exception:
        return False
    finally:
        s.close()


def layer(n, name, installed, utilized, tested, note=""):
    R[n] = dict(layer=name, installed=bool(installed), utilized=bool(utilized),
                tested=bool(tested), note=note[:150])


ps = sh("docker ps --format '{{.Names}}'")
imgs = sh("docker images --format '{{.Repository}}:{{.Tag}}'")

# 1 Nemotron 3.5 Lightning
# GUARD: this box is shared and already runs ~47 GB of resident inference.
# Loading a 24 GB GGUF without headroom wedged the machine on 2026-08-15.
# Never attempt the load test unless there is comfortable margin.
MODEL_GB = 24
HEADROOM_GB = 12


def free_gb():
    try:
        return int(sh("free -g | awk '/Mem:/{print $7}'").strip())
    except Exception:
        return 0


gguf = os.path.exists("%s/nvidia/nemotron-3.5-lightning/model.gguf" % HOME)
nvfp4 = "Lightning" in sh("ls %s/.cache/huggingface/hub 2>/dev/null" % HOME)
avail = free_gb()
if avail < MODEL_GB + HEADROOM_GB:
    loads, why = False, "SKIPPED load test: only %dG free, need %dG" % (
        avail, MODEL_GB + HEADROOM_GB)
else:
    # "did not fail to load" is NOT the same as working. Require real generated
    # text: the first attempt loaded cleanly and emitted nothing at all.
    gen = sh("cd %s/llama.cpp 2>/dev/null && timeout 200 ./build/bin/llama-cli "
             "-m %s/nvidia/nemotron-3.5-lightning/model.gguf "
             "-p 'The capital of France is' -n 12 -no-cnv --temp 0 2>/dev/null | "
             "tr -d '[:space:]' | head -c 200" % (HOME, HOME), t=220)
    # strip the echoed prompt; anything left is model output
    produced = gen.replace("ThecapitalofFranceis", "").strip()
    loads = len(produced) >= 3
    why = "generated=%r (%dG free)" % (produced[:40], avail)
layer(1, "Nemotron 3.5 Lightning", gguf or nvfp4, loads, loads,
      "gguf=%s nvfp4=%s %s" % (gguf, nvfp4, why))

# 2 NemoClaw + OpenShell
nc = sh("nemoclaw --version 2>&1 | head -1")
osh = sh("openshell --version 2>&1 | head -1")
sb = "openshell-" in ps
layer(2, "NemoClaw + OpenShell", "v" in nc, sb, sb, "%s / %s" % (nc, osh))

# 3 vLLM
st, body = http("http://127.0.0.1:8000/v1/models")
vllm_up = st == 200
st2, b2 = http("http://127.0.0.1:8000/v1/chat/completions",
               data=json.dumps({"model": "nvidia/Qwen3.6-35B-A3B-NVFP4",
                                "messages": [{"role": "user", "content": "hi"}],
                                "max_tokens": 8}).encode(), t=90)
layer(3, "vLLM serving", "nemoclaw-vllm" in ps, vllm_up, st2 == 200,
      "models=%s chat=%s" % (st, st2))

# 4 DSpark speculative decoding
spec = "speculative" in sh("docker inspect nemoclaw-vllm --format '{{json .Config.Cmd}}' 2>/dev/null")
layer(4, "DSpark spec-decoding", spec, spec, spec, "spec flags in vllm cmd: %s" % spec)

# 5 NeMo Switchyard
sw_inst = "0." in sh("%s/envs/switchyard/bin/pip show nemo-switchyard 2>/dev/null | "
                     "grep -i ^version" % HOME)
sw_up = port(8790)
st3, b3 = http("http://127.0.0.1:8790/v1/chat/completions",
               data=json.dumps({"model": "qwen-direct",
                                "messages": [{"role": "user", "content": "hi"}],
                                "max_tokens": 8}).encode(), t=120) if sw_up else (None, "")
layer(5, "NeMo Switchyard", sw_inst, sw_up, st3 == 200, "port8790=%s chat=%s" % (sw_up, st3))

# 6 VSS
vss_n = len([x for x in ps.split("\n") if x.startswith("vss-")])
st4, b4 = http("http://127.0.0.1:8002/health")
# Parse the list rather than grepping for "sensorId" — VST returns a JSON array
# whose identity key is "remoteDeviceId", so the substring count was always 0
# and reported a utilized layer as unused.
nsens = 0
try:
    _st, _b = http("http://127.0.0.1:7777/vst/api/v1/sensor/list", t=10)
    _j = json.loads(_b) if _b else []
    nsens = len(_j) if isinstance(_j, list) else 0
except Exception:
    nsens = 0
layer(6, "VSS 3.2.1", vss_n >= 5, nsens > 0, st4 == 200,
      "%d containers, %d sensors, health=%s" % (vss_n, nsens, st4))

# 7 DeepStream + RT-DETR
rtcv = "vss-rt-cv" in imgs                 # image really is vss-rt-cv:3.2.1-sbsa
# The CONTAINER is vss-rtvi-cv. "vss-rt-cv" is not a substring of "vss-rtvi-cv"
# (rt-vi-cv), so this scored a healthy 3-hour-old container as down.
rtcv_up = "vss-rtvi-cv" in ps or "vss-rt-cv" in ps
layer(7, "DeepStream + RT-DETR", rtcv, rtcv_up, rtcv_up, "image=%s running=%s" % (rtcv, rtcv_up))

# 8 Cosmos Reason
cos = "cosmos3-reasoner" in imgs
cos_up = "cosmos" in ps
layer(8, "Cosmos Reason 3 Nano", cos, cos_up, cos_up, "image=%s running=%s" % (cos, cos_up))

# 9 Cosmos-Embed1
emb = "rt-embed" in imgs or "cosmos-embed" in imgs
emb_up = "embed" in ps
layer(9, "Cosmos-Embed1", emb, emb_up, emb_up and port(8017), "image=%s running=%s" % (emb, emb_up))

# 10 Auto Calibration
cal = "auto-calibration" in imgs
cal_up = "calibration" in ps
calfile = os.path.exists("%s/plans/calibration.json" % HOME)
layer(10, "VSS Auto Calibration", cal, cal_up, calfile,
      "image=%s running=%s calibration.json=%s" % (cal, cal_up, calfile))

# 11 RT-CV-3D / MV3DT
mv = "mv3dt" in imgs
mv_up = "mv3dt" in ps
layer(11, "RT-CV-3D / MV3DT", mv, mv_up, mv_up, "image=%s running=%s" % (mv, mv_up))

# 12 3DGRUT
# Check the exit status, not the text. Grepping stderr for "Error" gave a FALSE
# PASS: `head -1` returned "Traceback (most recent call last):", which contains
# no such substring, so a missing module was scored green.
gcode = sh("%s/arlo-vision/bin/python -c 'import threedgrut' >/dev/null 2>&1; echo $?"
           % HOME).strip()
gok = gcode == "0"
gmsg = "import ok" if gok else "import failed (exit %s)" % gcode
layer(12, "3DGRUT / 3DGUT", gok, gok, gok, gmsg)

# 13 VSS Agent Skills / MCP
skills = len(sh("ls %s/src/video-search-and-summarization/skills 2>/dev/null" % HOME).split())
mcp_up = port(9988)
layer(13, "VSS Skills / MCP", skills > 0, mcp_up, mcp_up, "%d skills, mcp9988=%s" % (skills, mcp_up))

# 14 Build Vitals
bv = os.path.exists("%s/arlo-vision/build_vitals.py" % HOME)
bvj = "%s/plans/build_vitals.json" % HOME
bvr = os.path.exists(bvj)
bhi = json.load(open(bvj)).get("bhi") if bvr else None
layer(14, "Build Vitals (BHI)", bv, bvr, bhi is not None, "BHI=%s" % bhi)

# 15 Blueprint / spatial truth
have = all(os.path.exists("%s/plans/%s" % (HOME, f))
           for f in ("level1_rooms.json", "level2_rooms.json"))
exp = [f for f in ("building.glb", "building.ifc", "building.usda")
       if os.path.exists("%s/plans/%s" % (HOME, f))]
sg = os.path.exists("%s/plans/scenegraph.json" % HOME)
layer(15, "Blueprint / spatial truth", have, sg, len(exp) >= 2,
      "exports=%s scenegraph=%s" % (",".join(exp), sg))

green = sum(1 for v in R.values() if v["installed"] and v["utilized"] and v["tested"])
out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "green": green, "total": len(R), "layers": R}
json.dump(out, open("%s/plans/audit.json" % HOME, "w"), indent=1)

print("=" * 78)
print(" STACK AUDIT  %s      %d / %d fully green" % (out["ts"], green, len(R)))
print("=" * 78)
print(" %-3s %-26s %-4s %-4s %-4s  %s" % ("#", "layer", "inst", "util", "test", "note"))
for n in sorted(R):
    v = R[n]
    m = lambda b: " OK " if b else " -- "
    print(" %-3d %-26s %s %s %s  %s"
          % (n, v["layer"][:26], m(v["installed"]), m(v["utilized"]), m(v["tested"]), v["note"][:38]))
print("=" * 78)
print(" wrote ~/plans/audit.json")
