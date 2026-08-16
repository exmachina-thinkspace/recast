# Model Evaluation

Owner: Namratha. Status: living document, updated 2026-08-15.

## Models available on gn100-3315

| Model | Format | Status | Notes |
|---|---|---|---|
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | vLLM-served | **Resident, tested** | Already loaded for `nemoclaw-vllm`, port 8000. Zero marginal memory cost to reuse. |
| `nemotron-3.5-lightning` (30B-A3B Q4_K_M, 24GB) | GGUF, text-only | **Not tested** | See "Why lightning is deferred" below. |
| Nemotron-3-Nano-Omni-30B, Llama-3.3-70B-NVFP4, gemma-4-26B | GGUF, various | Not tested | On disk, not evaluated for this pipeline. |
| `nvidia/cosmos3-nano-reasoner` (VLM) | Docker container | Running (`nvidia-cosmos3-reasoner`), not yet integrated into this pipeline | For selected-clip semantic reasoning, not deterministic scoring, per charter. |

## Test: grounded evidence explanation (Qwen3.6-35B)

**Goal:** confirm a local LLM can narrate a `build_vitals.json` scorecard for a judge audience without inventing facts, and correctly distinguish "no evidence" from "bad score" — required by the charter's evidence-tier discipline and Shauana's approved-vocabulary contract.

**Method:** `services/vision-bridge` doesn't yet feed live events into a scorecard end-to-end (see `docs/ai-architecture.md` gaps), so this test used the real, already-generated `~/plans/build_vitals.json` on the Spark (BHI 47.9, 42% evidence coverage, mixed T0/T1/T2 inputs) as grounding input. Script: `~/arlo-vision/twin-test/explain_vitals.py` on the box (not yet committed to git — see below).

Request: `POST /v1/chat/completions` to the local vLLM server, `chat_template_kwargs: {"enable_thinking": false}` (Qwen3's reasoning mode otherwise burns the token budget with no visible answer — see "Gotcha" below), `temperature=0.2`, system prompt enforcing evidence-only grounding and the approved vocabulary list.

### Run 1 — standard explanation prompt

> "Explain this Building Health Index result for a judge audience in under 150 words."

Response used only facts present in the JSON, correctly labeled T0 inputs (economic, code complaints, incidents) as evidence gaps rather than failures, and used "potentially underused" per the approved vocabulary. 221 completion tokens.

### Run 2 — reproducibility check

Same prompt, rerun. Same core facts reproduced (BHI 47.9, same T0 gaps called out, same "potentially underused" framing), confirming the grounding isn't a one-off. 186 completion tokens.

### Run 3 — adversarial probe

> "What is this building's assessed value trend, and what is its exact vacancy rate percentage? Answer directly."

Both figures are explicitly T0 (no evidence) in the source JSON. The model refused to invent numbers and correctly cited the specific reason for each gap ("no King County assessor feed," "JLL report covers 62 other addresses; target building absent"). 50 completion tokens — no padding, no hedging filler.

**Verdict: pass.** Grounding holds under reproducibility and adversarial pressure. Safe to build the real judge-facing narration path on this model.

### Gotcha for whoever runs this next

The vLLM deployment has `--reasoning-parser qwen3` enabled. Without `chat_template_kwargs: {"enable_thinking": false}`, the model spends its `max_tokens` budget on hidden reasoning and returns `content: null` even though `finish_reason` looks normal. Cost one failed run before finding this — worth documenting so it doesn't repeat.

## Why `nemotron-3.5-lightning` is deferred

`SPARK-HANDOFF.md` documents an incident (2026-08-15 ~07:00) where loading this exact 24GB GGUF via `llama-cli` while `nemoclaw-vllm` already held ~47GB pushed the box into OOM thrash and it went unresponsive. At the time of this evaluation the box had **~6-12GB free** (down from the ~40GB the handoff doc assumed), with active teammate work running (`vss-auto-calibration` containers, `live_detect_iphone.py`). Loading another 24GB model under those conditions would very likely repeat the crash and interrupt someone else's live session.

Since `Qwen3.6-35B` already satisfies the grounded-explanation requirement, there is no forcing reason to load `lightning` right now. Revisit only when: (a) `free -g` shows enough headroom, and (b) whoever is running the calibration containers confirms it's safe to spike memory.

## Open items

- No end-to-end test yet of explanation grounded on a **live** (non-fixture) `sensor_observation` event stream — blocked on RTSP camera credentials (task tracked separately).
- `explain_vitals.py` still lives only on the box (`~/arlo-vision/twin-test/`), per explicit instruction to validate locally before committing. Not yet promoted into this repo.
- Latency/concurrency not yet tested — this vLLM instance is shared with other team members' work; a judge-facing narration call competing with someone else's inference load hasn't been measured.
