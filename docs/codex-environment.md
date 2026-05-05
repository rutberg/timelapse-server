# Codex Cloud Environment

Use these settings when creating the Codex environment for this repository.

## Container Image

- Image: `universal`
- Runtime package versions: pin Python to `3.11` or newer. Python `3.11` is closest to the Debian/Raspberry Pi deployment baseline.
- No Node.js setup is needed; the web UI uses vendored static assets.
- No database or external service is required for tests.

## Pre-installed Packages

The setup script installs the project-specific Debian packages:

- `ca-certificates`
- `ffmpeg`
- `openssh-client`

`ffmpeg` is required by video-generation code. `openssh-client` provides `ssh`, `scp`, and `ssh-keygen`, which the provisioning code and tests expect.

## Environment Variables

Configure these in Codex environment settings so they persist into the agent phase:

```bash
TIMELAPSE_DATA_DIR=/tmp/timelapse-data
TIMELAPSE_ALLOWED_NETWORKS=127.0.0.0/8,::1/128
TIMELAPSE_BIND_HOST=127.0.0.1
TIMELAPSE_PORT=8080
TIMELAPSE_VENV=.venv-dev
```

Optional:

```bash
CODEX_RUN_TESTS_DURING_SETUP=1
LOG_LEVEL=INFO
```

Leave `TIMELAPSE_PUBLIC_URL` unset unless a task explicitly needs to test public URL generation.

## Setup Script

Paste this into the Codex setup script field:

```bash
scripts/codex-setup.sh
```

Or paste the script contents directly from `scripts/codex-setup.sh`.

## Validation Command

Ask Codex to run this before opening a PR:

```bash
.venv-dev/bin/python -m pytest
```

Codex also reads `AGENTS.md`, which lists the setup, test, and development server commands for this repo.
