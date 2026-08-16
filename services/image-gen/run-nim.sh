#!/usr/bin/env bash
# Start / stop the FLUX.1-dev NVIDIA NIM on the Acer GN100 (DGX Spark).
#
#   ./run-nim.sh start [base|depth|canny]   # default variant: base
#   ./run-nim.sh wait                        # block until /v1/health/ready
#   ./run-nim.sh status | logs | stop
#
# Env (all optional except NGC_API_KEY):
#   NGC_API_KEY              NGC personal key -- required to pull the container + weights
#   HF_TOKEN                 Hugging Face token; needed if the container asks you to
#                            accept the FLUX.1-dev license (huggingface.co/black-forest-labs/FLUX.1-dev)
#   RECAST_NIM_PORT          host port for the NIM API           (default 8610; 8000 is vLLM)
#   RECAST_NIM_CACHE         where weights are cached            (default: SSD path if present, else ~/.cache/nim)
#   RECAST_NIM_TAG           container tag                       (default 1.2.3; DGX Spark profile needs >= 1.2.0)
#   RECAST_NIM_MIN_FREE_GB   refuse to start below this much free RAM (default 24)
#   RECAST_NIM_GPU_FLAGS     docker GPU flags (default "--device nvidia.com/gpu=all", the DGX Spark
#                            form; use "--runtime=nvidia --gpus all" if CDI is not configured)
#
# MEMORY -- READ THIS FIRST. The Spark has 128 GB unified memory shared by
# everyone's work (vLLM ~47 GB, cosmos3 reasoner, VSS containers, ...). Loading
# a 24 GB model on top of that put the box into OOM thrash on 2026-08-15
# (see docs/model-evaluation.md). This script checks `free -g` and refuses to
# start unless RECAST_NIM_MIN_FREE_GB is available. Do not lower the threshold
# without telling whoever else is running things on the box. When memory is
# tight, use the hosted backend (NVIDIA_API_KEY) and come back to this later.
#
# FLUX.1-dev NIM on DGX Spark uses the FP4 TensorRT profile (~16 GB min per
# NVIDIA's support matrix). First start downloads the weights (several GB) --
# put the cache on the SSD, not the root disk.
set -euo pipefail

NAME="recast-flux-nim"
IMAGE_REPO="nvcr.io/nim/black-forest-labs/flux.1-dev"
TAG="${RECAST_NIM_TAG:-1.2.3}"
PORT="${RECAST_NIM_PORT:-8610}"
MIN_FREE_GB="${RECAST_NIM_MIN_FREE_GB:-24}"
GPU_FLAGS="${RECAST_NIM_GPU_FLAGS:---device nvidia.com/gpu=all}"
CMD="${1:-start}"
VARIANT="${2:-${NIM_MODEL_VARIANT:-base}}"

if [[ -z "${RECAST_NIM_CACHE:-}" ]]; then
  if [[ -d /media/acer01/SB-XTM5/models ]]; then
    RECAST_NIM_CACHE=/media/acer01/SB-XTM5/models/nim
  else
    RECAST_NIM_CACHE="$HOME/.cache/nim"
  fi
fi

die() { echo "error: $*" >&2; exit 1; }
running() { command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' | grep -qx "$NAME"; }

free_gb() {
  # "available" column of free -g (Linux). On non-Linux just report a big number.
  if command -v free >/dev/null 2>&1; then
    free -g | awk '/^Mem:/ {print $7}'
  else
    echo 999
  fi
}

case "$CMD" in
  start)
    case "$VARIANT" in base|depth|canny) ;; *) die "variant must be base|depth|canny";; esac
    command -v docker >/dev/null || die "docker not found"
    [[ -n "${NGC_API_KEY:-}" ]] || die "NGC_API_KEY is not set (needed to pull the NIM). Do not paste keys into files; export it in your shell."
    if running; then echo "$NAME is already running on port $PORT"; exit 0; fi
    if ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$PORT\$"; then
      die "port $PORT is already in use; set RECAST_NIM_PORT to something else"
    fi
    avail="$(free_gb)"
    echo "free memory: ${avail} GB (threshold ${MIN_FREE_GB} GB)"
    if (( avail < MIN_FREE_GB )); then
      echo "--- what is using memory right now ---"
      docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}' 2>/dev/null || true
      die "only ${avail} GB free. Refusing to start the NIM. Coordinate with the team, free memory, or use the hosted backend (NVIDIA_API_KEY). Override with RECAST_NIM_MIN_FREE_GB only after checking with whoever is on the box."
    fi
    mkdir -p "$RECAST_NIM_CACHE"
    chmod 1777 "$RECAST_NIM_CACHE" 2>/dev/null || true
    echo "logging in to nvcr.io ..."
    echo "$NGC_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin >/dev/null
    echo "starting $NAME  variant=$VARIANT  tag=$TAG  port=$PORT  cache=$RECAST_NIM_CACHE"
    # shellcheck disable=SC2086
    docker run -d --rm --name "$NAME" \
      $GPU_FLAGS \
      --shm-size=16g \
      -e NGC_API_KEY \
      -e HF_TOKEN="${HF_TOKEN:-}" \
      -e NIM_MODEL_VARIANT="$VARIANT" \
      -p "$PORT:8000" \
      -v "$RECAST_NIM_CACHE:/opt/nim/.cache/" \
      "$IMAGE_REPO:$TAG" >/dev/null
    echo "started. First run downloads weights; warmup is ~3 min after that."
    echo "  ./run-nim.sh wait      # block until ready"
    echo "  ./run-nim.sh logs      # watch progress ('Pipeline warmup: done')"
    echo "  export RECAST_IMAGEGEN_NIM_URL=http://127.0.0.1:$PORT"
    ;;
  wait)
    url="http://127.0.0.1:$PORT/v1/health/ready"
    echo "waiting for $url ..."
    for i in $(seq 1 240); do
      if curl -fsS -o /dev/null "$url" 2>/dev/null; then echo "ready."; exit 0; fi
      running || die "$NAME is not running (see: docker logs $NAME)"
      sleep 5
    done
    die "not ready after 20 min; check ./run-nim.sh logs"
    ;;
  status)
    if running; then
      echo "$NAME running on port $PORT"
      curl -fsS "http://127.0.0.1:$PORT/v1/health/ready" >/dev/null 2>&1 && echo "health: ready" || echo "health: not ready yet"
    else
      echo "$NAME not running"
    fi
    echo "free memory: $(free_gb) GB"
    ;;
  logs)
    docker logs -f --tail 100 "$NAME"
    ;;
  stop)
    if running; then docker stop "$NAME" >/dev/null && echo "stopped $NAME"; else echo "$NAME not running"; fi
    ;;
  *)
    die "usage: $0 start [base|depth|canny] | wait | status | logs | stop"
    ;;
esac
