#!/usr/bin/env bash
set -euo pipefail

# The campaign supervisor owns the actuator lifecycle.  Keep the native
# xApp alive across transient E2/RIC exits; the rApp never starts or restarts
# this process itself.
ACTUATOR_BIN="$1"
CONFIG_FILE="$2"
FLEXRIC_LIB="$3"
XAPP_PORT="$4"

child_pid=""
terminating=0

stop_child() {
  terminating=1
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
}
trap stop_child TERM INT

while (( terminating == 0 )); do
  "$ACTUATOR_BIN" -c "$CONFIG_FILE" -p "$FLEXRIC_LIB/" -x "$XAPP_PORT" &
  child_pid=$!
  child_status=0
  wait "$child_pid" || child_status=$?
  child_pid=""
  (( terminating == 0 )) || break
  printf '[TASAM_SUPERVISOR] actuator exited code %s at %s; restarting in 2s\n' \
    "$child_status" "$(date --iso-8601=seconds)" >&2
  sleep 2
done

exit 0
