#!/bin/sh
# Start/stop the Mac menu-bar voice app. Uses the server's venv.
cd "$(dirname "$0")/.." || exit 1
PY="${MAC_APP_PYTHON:-.venv/bin/python}"
case "$1" in
  start) mkdir -p data; nohup "$PY" -m mac_app >> data/mac_app.log 2>&1 & echo "mac app started (log: data/mac_app.log)";;
  stop) pkill -f "python.* -m mac_app" && echo stopped;;
  *) echo "usage: $0 start|stop"; exit 2;;
esac
