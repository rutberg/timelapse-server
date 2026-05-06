# Security Policy

`timelapse-server` is a pre-1.0 hobby project intended for use on a trusted LAN.
The README's [Security Note](README.md#security-note) describes the deployment
threat model in detail; this file covers how to **report** issues responsibly.

## Supported versions

Because the project is pre-1.0 and ships from `main`, only the latest release
(and `main`) is supported. Fixes will land on `main` and roll forward into the
next tag — older alpha/beta tags will not receive backported patches.

| Version            | Supported          |
| ------------------ | ------------------ |
| `main` / latest    | :white_check_mark: |
| Older pre-releases | :x:                |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Use GitHub's private vulnerability reporting on this repository instead:

1. Go to <https://github.com/rutberg/timelapse-server/security/advisories/new>
2. Fill out the advisory form with reproduction steps, impact, and (if known)
   a suggested fix.

If GitHub private reporting is unavailable for any reason, you may instead open
a minimal public issue titled "Security: please contact me" with no details,
and a maintainer will reach out to arrange a private channel.

### What to include

- A clear description of the issue and the affected component
  (server HTTP API, web UI, agent, install/provisioning script, etc.).
- A reproduction: minimal steps, request payloads, or a small PoC.
- The version / commit you tested against.
- Your assessment of impact (confidentiality, integrity, availability,
  prerequisites such as LAN access or paired-agent state).

### Response expectations

This is a hobby project maintained on a best-effort basis. Rough targets:

- **Acknowledgement:** within 7 days.
- **Initial triage / severity assessment:** within 14 days.
- **Fix or mitigation plan:** depends on severity and complexity; we aim for
  weeks rather than months for anything exploitable in a default install.

You will be credited in the advisory and release notes unless you ask
otherwise.

## Scope

In scope:

- Remote code execution, authentication bypass affecting the documented
  deployment model, or privilege escalation in the server, agent, or
  install/provisioning scripts.
- Vulnerabilities that let an attacker on the **same LAN** escape the
  documented threat model — for example, an agent accepting commands from
  a server it was never paired with, or the install script clobbering files
  outside its declared install/data directories.
- Issues in the SSH provisioning flow that go beyond the documented TOFU
  behaviour (e.g. silently overwriting a previously-pinned host key).
- Sensitive data (sudo passwords, agent tokens, camera credentials) being
  written to disk, logs, or backups when the documented behaviour says they
  should not be.

Out of scope (these are documented design choices, not bugs):

- The HTTP API/UI having no built-in authentication. Access control is
  delegated to `TIMELAPSE_ALLOWED_NETWORKS` and the network layer.
- Anyone on the configured LAN/CIDR being able to capture, configure, or
  reprovision agents.
- `StrictHostKeyChecking=accept-new` (TOFU) on the very first SSH
  provisioning connection, as documented in the README.
- Reports requiring physical access to the Pi/server, or requiring the
  attacker to already be `root` on the host.
- Reports against deployments that expose the server directly to the public
  internet without the recommended hardening (reverse proxy with auth,
  Tailscale, VPN, etc.).

If you are unsure whether something is in scope, report it anyway and we'll
discuss.
