# Repository Instructions

This is a Python timelapse system with a FastAPI server in `server/` and a Raspberry Pi capture agent in `agent/`. The static web UI is vendored under `server/app/static/`; there is no Node.js build step.

## Setup

Use `scripts/codex-setup.sh` in Codex cloud environments. It creates `.venv-dev`, installs `requirements-dev.txt`, and tries to install the system packages needed by video rendering and provisioning code. If `apt-get` is blocked in the cloud setup environment, the script continues so Python tests can still run.

Useful environment defaults:

```bash
TIMELAPSE_DATA_DIR=/tmp/timelapse-data
TIMELAPSE_ALLOWED_NETWORKS=127.0.0.0/8,::1/128
TIMELAPSE_BIND_HOST=127.0.0.1
TIMELAPSE_PORT=8080
TIMELAPSE_VENV=.venv-dev
```

## Validation

Run the full test suite before opening a PR:

```bash
.venv-dev/bin/python -m pytest
```

For server-only changes, this narrower command is also useful while iterating:

```bash
.venv-dev/bin/python -m pytest tests/server
```

For agent-only changes:

```bash
.venv-dev/bin/python -m pytest tests/agent
```

## Development Server

Start the local FastAPI server with:

```bash
scripts/dev-server.sh start
```

Stop it with:

```bash
scripts/dev-server.sh stop
```

The server defaults to `http://127.0.0.1:8080`.
