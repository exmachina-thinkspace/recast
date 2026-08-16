#!/usr/bin/env bash
# Start the Spark app with enough GPU memory for Stable Diffusion.
#
# SD needs ~8 GiB free. A large idle inference container (nemoclaw-vllm held
# 50+ GiB) starved it, and generation failed with "insufficient memory" until it
# was stopped by hand. Reclaim automatically so it is never a manual step.
#
# Containers are STOPPED, never removed: `docker start <name>` restores them
# with image and volumes intact.
#
# PROTECTED holds the audited 15-layer stack (VSS, Cosmos Reason, their stores).
# Stopping those to free memory would trade a working demo for a failing audit,
# so they are never candidates no matter how large they grow.
set -u
NEED_GIB=${NEED_GIB:-12}
PROTECTED='^(vss-|nvidia-cosmos3|redis$|phoenix$)'

free_gib() { awk '/MemAvailable/ {printf "%.0f", $2/1048576}' /proc/meminfo; }

echo "[mem] available: $(free_gib) GiB (need ${NEED_GIB})"
if [ "$(free_gib)" -lt "$NEED_GIB" ]; then
  # Largest first, so one stop usually suffices. Selected by actual usage rather
  # than a hardcoded name list, which silently goes stale as containers change.
  CANDS=$(docker stats --no-stream --format '{{.MemUsage}}\t{{.Name}}' 2>/dev/null \
          | sed 's/GiB.*\t/\t/' | sort -rn \
          | awk -F'\t' '{print $2}' | grep -Ev "$PROTECTED")
  for c in $CANDS; do
    echo "[mem] stopping $c (restart later with: docker start $c)"
    docker stop "$c" >/dev/null 2>&1
    sleep 3
    [ "$(free_gib)" -ge "$NEED_GIB" ] && break
  done
  AFTER=$(free_gib)
  echo "[mem] after reclaim: ${AFTER} GiB"
  [ "$AFTER" -lt "$NEED_GIB" ] && \
    echo "[mem] WARNING: still under ${NEED_GIB} GiB - generation may fail. Only the audited stack remains and it is not stopped automatically."
else
  echo "[mem] enough already; nothing stopped"
fi

pkill -f 'spark_app\.py' 2>/dev/null; sleep 3
cd /home/acer01/arlo-vision || exit 1
DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority \
  nohup ./bin/python ./spark_app.py > app.log 2>&1 &
disown
echo "[app] started pid $!"
