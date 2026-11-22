#!/bin/sh
# Start one microservice from the shared runtime volume and restart it whenever its file changes.
#   SVC : runtime directory name (for example "cart", or "cart-canary" for the canary copy)
#   SRC : which known-good copy to start from (defaults to SVC)
#   PORT: the port to listen on
# watchfiles escalates to SIGKILL, so a patch that ignores shutdown signals cannot wedge a rollback.
set -e
SRC="${SRC:-$SVC}"
mkdir -p "/runtime/$SVC" /runtime/logs
[ -f "/runtime/$SVC/main.py" ] || cp "/golden/$SRC/main.py" "/runtime/$SVC/main.py"
watchfiles --filter python --sigint-timeout 2 --sigkill-timeout 1 \
  "uvicorn main:app --app-dir /runtime/$SVC --host 0.0.0.0 --port $PORT" "/runtime/$SVC" 2>&1 \
  | tee -a "/runtime/logs/$SVC.log"
