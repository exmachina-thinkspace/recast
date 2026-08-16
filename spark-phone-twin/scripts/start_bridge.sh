#!/usr/bin/env bash
# Start the phone bridge, detached so it survives the SSH session that starts it.
#
# Backgrounding `setsid nohup ... &` inline in an ssh command did NOT survive:
# the bridge died with the session and the phone silently lost its connection
# mid-test, which looked exactly like an app bug. Doing it inside a script --
# the same shape as start_app.sh, which has been reliable -- keeps it alive.
set -u
cd /home/acer01/arlo-vision || exit 1
pkill -f 'phone_bridge\.py' 2>/dev/null
sleep 3
nohup ./bin/python ./phone_bridge.py >> bridge.log 2>&1 &
disown
sleep 12
PID=$(pgrep -f 'phone_bridge\.py' | head -1)
if [ -z "$PID" ]; then
  echo "[bridge] FAILED to start; last log lines:"
  grep -v 'it/s' bridge.log | tail -6
  exit 1
fi
echo "[bridge] running pid $PID"
