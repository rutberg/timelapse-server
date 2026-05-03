# Server-Managed Pi Appliance — Phase Overview

This is an index for four phase plans, plus the parallelism strategy. Source PRD: `docs/PRD-server-managed-pi-appliance.md`.

## The four plans

| Phase | File | One-line goal |
|---|---|---|
| A | `2026-05-03-phase-a-agent-heartbeat.md` | Server tracks per-camera identity, status, and last-seen. Agent sends heartbeats. |
| B | `2026-05-03-phase-b-ssh-provisioning.md` | Server creates pending agents, generates SSH keys, and provisions Pis over SSH. |
| C | `2026-05-03-phase-c-update-pipeline.md` | Server publishes signed-checksum agent release bundles. Agent self-updates on poll. |
| D | `2026-05-03-phase-d-web-ui.md` | LAN web UI for create-agent wizard, monitoring, and capture controls. |

## Hard ordering constraints

```
        ┌─── Phase B (SSH provisioning)
Phase A ─┤
        ├─── Phase C (updates)
        │
        └─── Phase D (web UI) ← can start any time after A's contracts land
```

- **Phase A is foundation**. Every other phase reads/writes the camera-record fields it introduces. Land Phase A first.
- **Phase B and Phase C are independent** of each other once A is done. Different file areas, no shared state besides the camera record. **Run these two in parallel.**
- **Phase D** consumes API contracts from A, B, and C. Phase D is structured so the static-shell tasks (Tasks D1–D3) can begin in parallel with B/C as soon as Phase A's API contracts are stable. The dynamic pages (Tasks D4–D7) must wait until the relevant phase API exists, but the wiring is mechanical.

## Parallelism strategy within each plan

Each plan splits its tasks into **waves**:

- **Wave 1**: foundation tasks that must run first (usually a fixture, schema, or shared module).
- **Wave 2+**: parallel groups of independent tasks. Tasks within a wave touch different files and have no shared state — they can be dispatched to separate subagents simultaneously via `superpowers:dispatching-parallel-agents`.

Waves are marked at the top of each plan. Each task is bite-sized (2–5 minutes per step) and follows TDD where the change is testable.

## Suggested execution

The recommended cadence assumes a single human reviewer:

1. Execute **Phase A** sequentially using `superpowers:subagent-driven-development` (one fresh subagent per task). This phase introduces the test infra and the heartbeat contract — review every task carefully.
2. Execute **Phase B** and **Phase C** in parallel using two long-running subagents (one per plan). They do not share files; merge conflicts are limited to `server/app/main.py`'s router section and the `cameras` record schema. The schema is locked at the end of Phase A specifically so this is safe.
3. Execute **Phase D** last. By this point all backend contracts are stable; the UI work is mostly scaffolding + fetch wiring.

## Conventions used by all plans

- **Tests live under `tests/server/` and `tests/agent/`**. Phase A's first task creates the structure.
- **Server tests use `fastapi.testclient.TestClient` with a `tmp_path` data dir fixture.** No real filesystem mutation outside the fixture.
- **Agent tests use `unittest.mock` for `urlopen` and `subprocess.run`.** Capture is never actually invoked in tests.
- **One commit per task** — the commit step is always the final step. Squashing is a reviewer decision, not a planner decision.
- **No new top-level dependencies** are added without justification. Phase B adds `paramiko`. Phase C adds nothing. Phase D adds nothing (Alpine.js loads from a pinned CDN at runtime, served from `static/vendor/` so it works offline on the LXC).

## Out-of-band cleanup already done

The "Custom SD image builder" was removed before these plans were written:
- Deleted: `image/`, `agent/timelapse_firstboot_network.sh`, `agent/systemd/timelapse-firstboot-network.service`.
- README updated to point at Raspberry Pi Imager's native customisation panel for headless setup.

Plans assume that state.
