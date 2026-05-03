#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$ROOT/.dev-server.pid"
LOGFILE="$ROOT/.dev-server.log"
PORT="${TIMELAPSE_PORT:-8080}"
HOST="${TIMELAPSE_BIND_HOST:-127.0.0.1}"
DATA_DIR="${TIMELAPSE_DATA_DIR:-$ROOT/dev-data}"
ALLOWED="${TIMELAPSE_ALLOWED_NETWORKS:-127.0.0.0/8}"
VENV="${TIMELAPSE_VENV:-$ROOT/.venv-dev}"

usage() {
  cat <<USAGE
Usage: $0 {start|stop|restart|status|log}

Runs the Timelapse FastAPI server in the background, surviving shell exit.

  start    Launch uvicorn (refuses if already running). Writes PID to .dev-server.pid.
  stop     Kill the server.
  restart  stop + start.
  status   Show running state and tail of last log line.
  log      tail -f the log file.

Env overrides:
  TIMELAPSE_PORT (default $PORT)
  TIMELAPSE_BIND_HOST (default $HOST)
  TIMELAPSE_DATA_DIR (default $DATA_DIR)
  TIMELAPSE_ALLOWED_NETWORKS (default $ALLOWED)
  TIMELAPSE_VENV (default $VENV)
USAGE
}

is_running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

cmd_start() {
  if is_running; then
    echo "Already running (pid $(cat "$PIDFILE")). Use 'restart' to recycle."
    return 0
  fi
  if [ ! -x "$VENV/bin/uvicorn" ]; then
    echo "uvicorn not found at $VENV/bin/uvicorn. Create the venv first:" >&2
    echo "  python3 -m venv $VENV && $VENV/bin/pip install -r requirements-dev.txt" >&2
    return 1
  fi
  mkdir -p "$DATA_DIR"
  cd "$ROOT"
  TIMELAPSE_DATA_DIR="$DATA_DIR" \
  TIMELAPSE_BIND_HOST="$HOST" \
  TIMELAPSE_PORT="$PORT" \
  TIMELAPSE_ALLOWED_NETWORKS="$ALLOWED" \
    nohup "$VENV/bin/uvicorn" app.main:app \
      --app-dir server \
      --host "$HOST" \
      --port "$PORT" \
      --reload \
      --reload-dir server \
      >> "$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  disown
  sleep 1
  if is_running; then
    echo "Started pid $(cat "$PIDFILE") on http://$HOST:$PORT (log: $LOGFILE)"
  else
    echo "Failed to start. See $LOGFILE." >&2
    rm -f "$PIDFILE"
    return 1
  fi
}

cmd_stop() {
  if ! is_running; then
    echo "Not running."
    rm -f "$PIDFILE"
    return 0
  fi
  local pid
  pid="$(cat "$PIDFILE")"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 10); do
    if ! kill -0 "$pid" 2>/dev/null; then break; fi
    sleep 0.5
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "Forcing kill of pid $pid"
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
  pkill -f "uvicorn app.main" 2>/dev/null || true
  echo "Stopped."
}

cmd_status() {
  if is_running; then
    echo "Running (pid $(cat "$PIDFILE")) on http://$HOST:$PORT"
    tail -n 1 "$LOGFILE" 2>/dev/null || true
  else
    echo "Not running."
  fi
}

cmd_log() {
  exec tail -f "$LOGFILE"
}

case "${1:-}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  log)     cmd_log ;;
  ""|-h|--help) usage ;;
  *)       usage; exit 1 ;;
esac
