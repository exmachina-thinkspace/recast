# Recast image generation (FLUX.1-dev on the Spark)

Renders the "Alternative Futures" step of the Recast story: take a real space
(or just a description) and show what it could become. Same model either way:

| Backend | What it is | When to use it |
|---|---|---|
| `nim` | **FLUX.1-dev NVIDIA NIM running on the Acer GN100 / DGX Spark.** Official DGX Spark TensorRT FP4 profile (NIM `1.2.0+`). Nothing leaves the box. | The judge-facing path. Start it with `run-nim.sh` when the box has memory to spare. |
| `hosted` | The same model on build.nvidia.com (`NVIDIA_API_KEY`). | Building UI / integration now, while the box is memory-tight. Flip to `nim` later with no code change. |

Everything here is stdlib Python (like the other services). Pillow is optional
(converts reference photos to PNG and shrinks them for the hosted size limit).

**Status (2026-08-15):** client, service and test page are verified end-to-end
against a mock of the NIM contract. The live NIM path on gn100-3315 and the
hosted path have **not** been run yet (no free memory on the box, no API key on
the laptop that wrote this). The request/response contract comes from NVIDIA's
NIM docs and NVIDIA's own ComfyUI NIM client, so wiring should be right, but
the first person to run it live should confirm timings and update this note.

## Files

| File | Purpose |
|---|---|
| `imagegen.py` | Library + CLI. `generate(...)` -> image bytes; `recast_prompt(...)` prompt helper; backend auto-selection. |
| `server.py` | HTTP service on **port 8602** (`POST /generate`, `GET /health`, test page at `/`). |
| `index.html` | Browser test page served by `server.py`. Prompt, mode, reference photo upload, timings. |
| `run-nim.sh` | Start/stop/wait/status/logs for the FLUX.1-dev NIM container on the Spark, with a memory preflight. |
| `out/` | Generated images (gitignored). |

Ports: NIM container -> host **8610** (8000 is vLLM), service -> **8602**
(8600 api-sink, 8601 voice-agent, 8900 frontend-api).

## Quick start

### A. Right now, from any laptop (hosted backend)

```bash
export NVIDIA_API_KEY=nvapi-...          # from build.nvidia.com; never commit it
cd services/image-gen
python3 imagegen.py --check              # shows nim: not ready, hosted: configured
python3 imagegen.py --recast "Lake Union Building" "vacant office floor" "mixed-income apartments"
# -> out/20260815-....-base.jpg
python3 server.py --port 8602            # then open http://localhost:8602/
```

### B. On the Spark (local NIM)

```bash
ssh <you>@gn100-3315
cd ~/recast/services/image-gen           # wherever the repo is checked out on the box
export NGC_API_KEY=...                   # NGC personal key (organizers sent instructions)
export HF_TOKEN=...                      # only if the container asks for the FLUX.1-dev license
./run-nim.sh status                      # shows free memory
./run-nim.sh start base                  # refuses if < 24 GB free -- see Memory below
./run-nim.sh wait                        # first run downloads weights; then ~3 min warmup
python3 imagegen.py --check              # nim: ready
python3 imagegen.py "a bright co-working lobby in a converted 1980s office tower"
python3 server.py --port 8602            # http://gn100-3315:8602/ (or via Tailscale)
```

`./run-nim.sh start depth` / `start canny` for the structure-preserving modes
(one variant per container; stop and restart to switch).

## Modes

| mode | input | what you get | Recast use |
|---|---|---|---|
| `base` | prompt | concept image | "what could a floor like this become" |
| `depth` | prompt + photo | keeps the photo's spatial layout, re-renders everything else | photograph the actual lobby / floorplate -> render it as housing, clinic, maker space |
| `canny` | prompt + photo | keeps the photo's edges (columns, mullions, ceiling grid) | same, tighter to the existing structure |

Depth/canny is the interesting one for the demo: the columns and window walls
in the render are the building's own, so the image reads as *this* building's
future, not a stock render. Pair it with a frame from the VSS/iPhone walkthrough.

## Calling it from your code

**Python (in-process, no server):**
```python
import sys; sys.path.insert(0, "services/image-gen")
from imagegen import generate, recast_prompt

r = generate(recast_prompt("Lake Union Building", "vacant office floor", "community health clinic"),
             mode="depth", image="lobby.jpg", steps=30, seed=7)
open("future.png", "wb").write(r.image_bytes)     # r.backend, r.elapsed_s, r.mime, r.seed
```

**HTTP (React frontend, voice agent, anything):**
```js
const res = await fetch("http://gn100-3315:8602/generate", {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({
    building: "Lake Union Building", current_use: "vacant office floor",
    proposed_use: "mixed-income apartments",          // or just {prompt: "..."}
    mode: "depth", image: dataUrlFromPhoto,           // omit both for mode "base"
    steps: 30, seed: 7,
  }),
});
const j = await res.json();          // {ok, image_b64, mime, backend, elapsed_s, seed, prompt, file}
img.src = `data:${j.mime};base64,${j.image_b64}`;
// or POST /generate?format=image to get raw bytes for a blob URL
```

`GET /health` tells you which backend will be used before you commit to a
30-second call. Generated files are also served at `/out/<file>`.

**Request fields:** `prompt` (or `building`+`current_use`+`proposed_use`+`extras`),
`mode` base|depth|canny, `image` (base64 or data URL; depth/canny only),
`width`/`height` (768–1344, multiples of 64; snapped if not), `steps` 5–100
(default 30), `cfg_scale` (1–9; default 3.5 base / 7 depth,canny), `seed`
(default 0; keep fixed for reproducible demo renders), `backend` auto|nim|hosted.

## Memory on the Spark -- read before `run-nim.sh start`

The box's 128 GB is shared: vLLM (Qwen3.6-35B) holds ~47 GB, plus the cosmos3
reasoner and VSS containers. Loading one more 24 GB model on 2026-08-15 put the
box into OOM thrash and took it down for everyone
(`docs/model-evaluation.md`). The FLUX.1-dev Spark profile needs ~16 GB.

- `run-nim.sh` refuses to start unless `free -g` shows >= 24 GB available
  (`RECAST_NIM_MIN_FREE_GB` to change it -- only after checking with whoever
  else is on the box).
- Generation competes with vLLM/VSS for the GPU. Expect slower renders while
  other inference is running; the community datapoint for FLUX-class models on
  Spark is roughly 25–40 s per 1024² image when the GPU is otherwise idle.
- Weights cache to the SSD (`/media/acer01/SB-XTM5/models/nim`) if that mount
  exists, else `~/.cache/nim`. Override with `RECAST_NIM_CACHE`.
- Stop it when you are done: `./run-nim.sh stop`.

Until there is headroom, build against `hosted` -- the API is identical.

## Hosted-backend limits

- `NVIDIA_API_KEY` from build.nvidia.com (free credits). Keep it in your shell,
  not in the repo (`.gitignore` already blocks `.env`, `*secret*`, `*-key.md`).
- Inline reference images must be small (NVIDIA rejects inline base64 above
  roughly 180 KB). With Pillow installed the client downsizes automatically;
  without it, send a small PNG. Larger uploads need NVIDIA's asset-upload flow,
  which is not implemented here (not needed on the local NIM).
- `cfg_scale` must be 1 < x <= 9 and `steps` 5–100 on hosted; the client
  clamps steps and uses defaults inside that range.
- Hosted returns JPEG; the local NIM returns PNG or JPEG. `r.mime` / `j.mime`
  tells you which.

## Judging / rules notes

- Spark track explicitly welcomes generative work; the "no AI-generated video"
  rule is about the *submission video*, not images inside the app.
- Judges weight local processing on the Spark, so the `nim` backend is what to
  show; `hosted` is scaffolding.
- FLUX.1-dev is under the FLUX.1 [dev] Non-Commercial License -- fine for the
  hackathon, but if Recast becomes a product, swap to FLUX.1-schnell
  (Apache-2.0; NIM `nvcr.io/nim/black-forest-labs/flux.1-schnell`, also has a
  Spark profile) or SD 3.5 Large. Only the container name and steps (schnell:
  1–4) change.

## References

- NIM Visual GenAI models + DGX Spark profiles: https://docs.nvidia.com/nim/visual-genai/latest/models.html
- NIM getting started (docker command, Spark `--device nvidia.com/gpu=all` note): https://docs.nvidia.com/nim/visual-genai/latest/getting-started.html
- Hosted API reference (request/response schema): https://docs.api.nvidia.com/nim/reference/black-forest-labs-flux_1-dev-infer
- NVIDIA's ComfyUI NIM client (same `/v1/infer` contract, `data:image/png;base64` input, `artifacts[0].base64` output): https://github.com/Comfy-Org/NIMnodes
- DGX Spark ComfyUI playbook (alternative route: SDXL/Flux in ComfyUI): https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/comfy-ui
