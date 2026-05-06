# Contributing to timelapse-server

Thanks for your interest! This is a small hobby project, but contributions —
bug reports, fixes, docs, new camera support, UI polish — are welcome.

## Before you start

- **Security issues:** do not file a public issue. Follow [SECURITY.md](SECURITY.md).
- **Big changes:** open an issue first to discuss the design before sinking
  time into a large PR. For small fixes a PR is fine.
- **Licence:** by submitting a contribution you agree that it is licensed
  under the project's [MIT License](LICENSE).

## Development setup

The project targets **Python 3.11+** and is run from source.

```bash
git clone https://github.com/rutberg/timelapse-server.git
cd timelapse-server

# Sets up .venv-dev and installs requirements-dev.txt.
# Tries to apt-get system deps for video rendering / provisioning;
# continues if apt is unavailable so Python tests still run.
./scripts/codex-setup.sh
```

Useful environment defaults for local development:

```bash
TIMELAPSE_DATA_DIR=/tmp/timelapse-data
TIMELAPSE_ALLOWED_NETWORKS=127.0.0.0/8,::1/128
TIMELAPSE_BIND_HOST=127.0.0.1
TIMELAPSE_PORT=8080
TIMELAPSE_VENV=.venv-dev
```

To run the server in the background during development:

```bash
./scripts/dev-server.sh start    # launches uvicorn, writes PID + log
./scripts/dev-server.sh log      # tail -f the log
./scripts/dev-server.sh restart  # after code changes
./scripts/dev-server.sh stop
```

To exercise the DSLR/agent paths without real hardware:

```bash
./scripts/simulate-dslr.sh dslr-test http://127.0.0.1:8080 30
```

## Tests

Always run the full suite before opening a PR:

```bash
.venv-dev/bin/python -m pytest
```

Narrower commands for faster iteration:

```bash
.venv-dev/bin/python -m pytest tests/server     # server only
.venv-dev/bin/python -m pytest tests/agent      # agent only
.venv-dev/bin/python -m pytest -k <pattern>     # by test name
```

If you add behaviour, add a test. If you fix a bug, add a regression test
that fails before your fix and passes after.

## Style and formatting

There is no enforced linter or formatter yet. Match the style of the file
you are editing:

- Python: standard library + FastAPI/Pydantic patterns already in `server/`
  and `agent/`. Type hints where the surrounding code uses them.
- JavaScript (server `static/v2/views/`): plain modules, no build step.
- Shell (`scripts/`): `#!/usr/bin/env bash` + `set -euo pipefail`.

Keep changes focused — avoid drive-by reformatting in the same commit as a
behaviour change.

## Commit messages

Follow the existing conventional-commit style visible in `git log`:

```
type(scope): short imperative summary

Optional body explaining why, wrapped at ~72 columns.
```

Common types in this repo: `feat`, `fix`, `docs`, `chore`, `ui`, `refactor`,
`test`. Scopes are loose (`dslr`, `agent`, `server`, `ui`, …); skip the
scope if no obvious one applies.

## Branches and pull requests

- Branch off `main`. Name branches descriptively
  (`feature/<thing>`, `fix/<thing>`, etc.).
- Push your branch and open a PR against `main`.
- In the PR description, include:
  - **What** changed and **why**.
  - **How to test** — exact commands or click-paths.
  - Linked issue(s), if any.
  - Screenshots/GIFs for UI changes.
- Keep PRs reasonably small and focused. If a change grows, split it.
- The maintainer may rebase, squash, or amend before merging.

## Reporting bugs and requesting features

Open a GitHub issue with:

- What you expected vs. what happened.
- Steps to reproduce (commands, request payloads, log excerpts).
- Environment: OS, Python version, server commit, hardware (camera model,
  Pi model) where relevant.
- For UI bugs, a screenshot or short screen recording helps a lot.

## A note on scope

`timelapse-server` is built and tested against a specific home-LAN setup
(server on Linux/Debian, Pi-based agents, USB DSLRs / RTSP IP cameras).
Patches that work outside that setup are welcome, but maintainers may not
be able to test exotic hardware combinations — please include enough
detail for someone else to reproduce.
