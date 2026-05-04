# Phase B: SSH Provisioning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Phase A complete and merged. This plan assumes `CameraRecord`, `safe_identifier`, the test scaffolding, and the heartbeat endpoint exist.

**Goal:** Server creates pending agents with per-agent SSH keypairs. Server provisions a Pi over SSH using that keypair, installs the agent, and archives the private key. After successful check-in the key moves to encrypted archival; SSH stays enabled by default (opt-in disable).

**Architecture:** New `server/app/agents.py` for pending-agent persistence. New `server/app/ssh_provision.py` for the SSH executor (shells out to OpenSSH — no new Python deps). New endpoints under `/api/agents`. Provisioning runs a single generated bash script piped over SSH so the whole install is atomic.

**Tech Stack:** OpenSSH client (`ssh-keygen`, `ssh`, `scp`), already installable via apt. No new Python packages.

---

## Wave structure

```
Wave 1 (sequential):  B1 → B2
Wave 2 (parallel):    [B3] [B4] [B5]    ← three subagents, three different files
Wave 3 (sequential):  B6 → B7 → B8
```

- B3 modifies `server/app/main.py` (adds the create-pending endpoint).
- B4 creates `server/app/provision_script.py` (script template; new file).
- B5 creates `server/app/ssh_provision.py` (SSH executor; new file).
- B6 wires B5 into `main.py` (must wait for B5 to land).

---

## File structure

**New files:**
- `server/app/agents.py` — `PendingAgent`, `AgentStore` (JSON-on-disk persistence under `DATA_DIR/agents/`).
- `server/app/ssh_keys.py` — `generate_keypair(agent_dir)` shelling out to `ssh-keygen`.
- `server/app/ssh_provision.py` — `provision_pi(agent, repo_root)` driving ssh/scp.
- `server/app/provision_script.py` — `build_install_script(camera_id, server_url, agent_version)` returning the bash payload.
- `tests/server/test_agents_store.py`
- `tests/server/test_ssh_keys.py`
- `tests/server/test_ssh_provision.py`
- `tests/server/test_provision_script.py`
- `tests/server/test_create_agent_endpoint.py`
- `tests/server/test_provision_endpoint.py`

**Modified files:**
- `server/app/main.py` — mount endpoints for `/api/agents`.
- `scripts/install-server.sh` — add `openssh-client` to the apt install list.
- `README.md` — Create-Agent walkthrough with Raspberry Pi Imager screenshots-text.

---

### Task B1: Pending-agent persistence (wave 1)

**Files:**
- Create: `server/app/agents.py`
- Create: `tests/server/test_agents_store.py`

- [ ] **Step 1: Write the failing tests**

`tests/server/test_agents_store.py`:
```python
from datetime import datetime
from pathlib import Path

from app.agents import AgentStore, PendingAgent


def test_create_and_load_pending_agent(tmp_data_dir: Path):
    store = AgentStore(tmp_data_dir)
    agent = store.create(
        agent_id="tomatoes-zero-w",
        display_name="Tomato Cam",
        expected_hostname="timelapse-tomatoes",
        ip_fallback=None,
        ssh_user="pi",
    )
    assert agent.status == "pending"
    assert agent.created_at is not None
    assert (tmp_data_dir / "agents" / "tomatoes-zero-w").is_dir()

    reloaded = AgentStore(tmp_data_dir).get("tomatoes-zero-w")
    assert reloaded.expected_hostname == "timelapse-tomatoes"
    assert reloaded.ssh_user == "pi"


def test_duplicate_agent_id_rejected(tmp_data_dir: Path):
    store = AgentStore(tmp_data_dir)
    store.create(
        agent_id="dup",
        display_name="x",
        expected_hostname="dup",
        ip_fallback=None,
        ssh_user="pi",
    )
    import pytest
    with pytest.raises(ValueError, match="already exists"):
        store.create(
            agent_id="dup",
            display_name="x2",
            expected_hostname="dup2",
            ip_fallback=None,
            ssh_user="pi",
        )


def test_update_status_persists(tmp_data_dir: Path):
    store = AgentStore(tmp_data_dir)
    store.create(
        agent_id="a",
        display_name="A",
        expected_hostname="a",
        ip_fallback=None,
        ssh_user="pi",
    )
    store.update_status("a", status="provisioning", last_provision_error=None)

    reloaded = AgentStore(tmp_data_dir).get("a")
    assert reloaded.status == "provisioning"


def test_list_returns_all_agents_sorted(tmp_data_dir: Path):
    store = AgentStore(tmp_data_dir)
    for agent_id in ("c", "a", "b"):
        store.create(
            agent_id=agent_id,
            display_name=agent_id,
            expected_hostname=agent_id,
            ip_fallback=None,
            ssh_user="pi",
        )
    assert [agent.agent_id for agent in store.list()] == ["a", "b", "c"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/server/test_agents_store.py -v`
Expected: ImportError — `app.agents` does not exist.

- [ ] **Step 3: Implement `server/app/agents.py`**

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


@dataclass
class PendingAgent:
    agent_id: str
    display_name: str
    expected_hostname: str
    ssh_user: str
    ip_fallback: Optional[str] = None
    status: str = "pending"
    created_at: Optional[str] = None
    last_provision_attempt_at: Optional[str] = None
    last_provision_error: Optional[str] = None


class AgentStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "agents"
        self.root.mkdir(parents=True, exist_ok=True)

    def _agent_dir(self, agent_id: str) -> Path:
        return self.root / agent_id

    def _manifest_path(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "manifest.json"

    def create(
        self,
        agent_id: str,
        display_name: str,
        expected_hostname: str,
        ip_fallback: Optional[str],
        ssh_user: str,
    ) -> PendingAgent:
        if self._manifest_path(agent_id).exists():
            raise ValueError(f"Agent {agent_id} already exists")
        agent = PendingAgent(
            agent_id=agent_id,
            display_name=display_name,
            expected_hostname=expected_hostname,
            ip_fallback=ip_fallback,
            ssh_user=ssh_user,
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        self._agent_dir(agent_id).mkdir(parents=True, exist_ok=True)
        self._write(agent)
        return agent

    def get(self, agent_id: str) -> PendingAgent:
        path = self._manifest_path(agent_id)
        if not path.exists():
            raise KeyError(agent_id)
        return PendingAgent(**json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> List[PendingAgent]:
        return sorted(
            (
                PendingAgent(**json.loads((self.root / d.name / "manifest.json").read_text(encoding="utf-8")))
                for d in self.root.iterdir()
                if (self.root / d.name / "manifest.json").exists()
            ),
            key=lambda a: a.agent_id,
        )

    def update_status(
        self,
        agent_id: str,
        *,
        status: str,
        last_provision_error: Optional[str],
    ) -> PendingAgent:
        agent = self.get(agent_id)
        agent.status = status
        agent.last_provision_attempt_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        agent.last_provision_error = last_provision_error
        self._write(agent)
        return agent

    def delete(self, agent_id: str) -> None:
        path = self._manifest_path(agent_id)
        if path.exists():
            path.unlink()

    def _write(self, agent: PendingAgent) -> None:
        path = self._manifest_path(agent.agent_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(agent), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/server/test_agents_store.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add server/app/agents.py tests/server/test_agents_store.py
git commit -m "feat(server): pending-agent JSON store"
```

---

### Task B2: SSH key generation (wave 1)

**Files:**
- Create: `server/app/ssh_keys.py`
- Create: `tests/server/test_ssh_keys.py`

- [ ] **Step 1: Write the failing tests**

`tests/server/test_ssh_keys.py`:
```python
import os
import shutil
from pathlib import Path

import pytest

from app.ssh_keys import generate_keypair, read_public_key


pytestmark = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None,
    reason="ssh-keygen not available",
)


def test_generate_keypair_writes_two_files(tmp_path: Path):
    private_path, public_path = generate_keypair(tmp_path, comment="agent-test")

    assert private_path.exists()
    assert public_path.exists()
    assert private_path.name == "id_ed25519"
    assert public_path.name == "id_ed25519.pub"


def test_private_key_is_chmod_600(tmp_path: Path):
    private_path, _ = generate_keypair(tmp_path, comment="agent-test")
    mode = os.stat(private_path).st_mode & 0o777
    assert mode == 0o600


def test_public_key_starts_with_ssh_ed25519(tmp_path: Path):
    _, public_path = generate_keypair(tmp_path, comment="timelapse-agent-test")
    text = read_public_key(public_path)
    assert text.startswith("ssh-ed25519 ")
    assert "timelapse-agent-test" in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/server/test_ssh_keys.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `server/app/ssh_keys.py`**

```python
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Tuple


SAFE_COMMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def generate_keypair(target_dir: Path, comment: str) -> Tuple[Path, Path]:
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    private_path = target_dir / "id_ed25519"
    public_path = target_dir / "id_ed25519.pub"

    if private_path.exists() or public_path.exists():
        raise FileExistsError(f"Keypair already exists in {target_dir}")

    sanitized_comment = SAFE_COMMENT_RE.sub("-", comment).strip("-") or "timelapse-agent"

    subprocess.run(
        [
            "ssh-keygen",
            "-t", "ed25519",
            "-N", "",
            "-f", str(private_path),
            "-C", sanitized_comment,
        ],
        check=True,
        capture_output=True,
    )
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o644)
    return private_path, public_path


def read_public_key(public_path: Path) -> str:
    return Path(public_path).read_text(encoding="utf-8").strip()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/server/test_ssh_keys.py -v`
Expected: 3 passed (or 3 skipped if ssh-keygen is missing — install `openssh-client` and retry).

- [ ] **Step 5: Commit**

```bash
git add server/app/ssh_keys.py tests/server/test_ssh_keys.py
git commit -m "feat(server): per-agent ed25519 keypair generation via ssh-keygen"
```

---

### Task B3: `POST /api/agents` create-pending endpoint (wave 2, server branch)

**Files:**
- Modify: `server/app/main.py`
- Test: `tests/server/test_create_agent_endpoint.py`

- [ ] **Step 1: Write the failing tests**

`tests/server/test_create_agent_endpoint.py`:
```python
import shutil

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None,
    reason="ssh-keygen not available",
)


def test_create_agent_returns_public_key_and_metadata(client):
    response = client.post(
        "/api/agents",
        json={
            "agent_id": "tomatoes-zero-w",
            "display_name": "Tomato Cam",
            "expected_hostname": "timelapse-tomatoes",
            "ip_fallback": None,
            "ssh_user": "pi",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["agent_id"] == "tomatoes-zero-w"
    assert body["status"] == "pending"
    assert body["public_key"].startswith("ssh-ed25519 ")
    assert body["expected_hostname"] == "timelapse-tomatoes"


def test_create_agent_rejects_duplicate(client):
    payload = {
        "agent_id": "dup",
        "display_name": "x",
        "expected_hostname": "dup-host",
        "ip_fallback": None,
        "ssh_user": "pi",
    }
    client.post("/api/agents", json=payload)
    response = client.post("/api/agents", json=payload)
    assert response.status_code == 409


def test_create_agent_rejects_bad_id(client):
    response = client.post(
        "/api/agents",
        json={
            "agent_id": "has space",
            "display_name": "x",
            "expected_hostname": "host",
            "ip_fallback": None,
            "ssh_user": "pi",
        },
    )
    assert response.status_code == 400


def test_list_agents_returns_created(client):
    client.post(
        "/api/agents",
        json={
            "agent_id": "a1",
            "display_name": "A1",
            "expected_hostname": "a1",
            "ip_fallback": None,
            "ssh_user": "pi",
        },
    )
    response = client.get("/api/agents")
    assert response.status_code == 200
    agents = response.json()["agents"]
    assert any(a["agent_id"] == "a1" for a in agents)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/server/test_create_agent_endpoint.py -v`
Expected: 404 errors.

- [ ] **Step 3: Add Pydantic models and endpoints to `server/app/main.py`**

Add the imports near the top (next to existing imports):
```python
from app.agents import AgentStore, PendingAgent
from app.ssh_keys import generate_keypair, read_public_key
```

Add models after `CheckinRequest`:
```python
HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,253}$")


class CreateAgentRequest(BaseModel):
    agent_id: str
    display_name: str
    expected_hostname: str
    ip_fallback: Optional[str] = None
    ssh_user: str = "pi"


def validate_hostname(value: str) -> str:
    if not HOSTNAME_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid hostname")
    return value


def validate_ip(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        ip_address(value)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid IP address") from error
    return value


def validate_ssh_user(value: str) -> str:
    if not re.match(r"^[a-z_][a-z0-9_-]{0,30}$", value):
        raise HTTPException(status_code=400, detail="Invalid SSH user")
    return value
```

Add endpoints near the other camera endpoints:
```python
def agent_store() -> AgentStore:
    return AgentStore(DATA_DIR)


def agent_to_response(agent: PendingAgent, include_public_key: bool = False) -> Dict[str, Any]:
    body = {
        "agent_id": agent.agent_id,
        "display_name": agent.display_name,
        "expected_hostname": agent.expected_hostname,
        "ip_fallback": agent.ip_fallback,
        "ssh_user": agent.ssh_user,
        "status": agent.status,
        "created_at": agent.created_at,
        "last_provision_attempt_at": agent.last_provision_attempt_at,
        "last_provision_error": agent.last_provision_error,
    }
    if include_public_key:
        public_path = DATA_DIR / "agents" / agent.agent_id / "id_ed25519.pub"
        if public_path.exists():
            body["public_key"] = read_public_key(public_path)
    return body


@app.post("/api/agents", status_code=201)
def create_agent(payload: CreateAgentRequest) -> Dict[str, Any]:
    agent_id = safe_identifier(payload.agent_id)
    expected_hostname = validate_hostname(payload.expected_hostname)
    ip_fallback = validate_ip(payload.ip_fallback)
    ssh_user = validate_ssh_user(payload.ssh_user)

    store = agent_store()
    try:
        agent = store.create(
            agent_id=agent_id,
            display_name=payload.display_name,
            expected_hostname=expected_hostname,
            ip_fallback=ip_fallback,
            ssh_user=ssh_user,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    generate_keypair(
        DATA_DIR / "agents" / agent_id,
        comment=f"timelapse-agent-{agent_id}",
    )
    return agent_to_response(agent, include_public_key=True)


@app.get("/api/agents")
def list_agents() -> Dict[str, Any]:
    return {"agents": [agent_to_response(agent) for agent in agent_store().list()]}


@app.get("/api/agents/{agent_id}")
def read_agent(agent_id: str) -> Dict[str, Any]:
    agent_id = safe_identifier(agent_id)
    try:
        agent = agent_store().get(agent_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Agent not found") from error
    return agent_to_response(agent, include_public_key=True)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/server/test_create_agent_endpoint.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add server/app/main.py tests/server/test_create_agent_endpoint.py
git commit -m "feat(server): POST /api/agents creates pending agent with keypair"
```

---

### Task B4: Provisioning script template (wave 2, script branch)

**Files:**
- Create: `server/app/provision_script.py`
- Create: `tests/server/test_provision_script.py`

- [ ] **Step 1: Write the failing tests**

`tests/server/test_provision_script.py`:
```python
from app.provision_script import build_install_script


def test_script_contains_camera_id_in_config():
    script = build_install_script(
        camera_id="tomatoes",
        server_url="http://192.168.1.10:8080",
        agent_version="0.3.0",
    )
    assert '"camera_id": "tomatoes"' in script
    assert "http://192.168.1.10:8080" in script


def test_script_uses_set_e_and_pipefail():
    script = build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.3.0",
    )
    assert script.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")


def test_script_installs_camera_packages_and_enables_service():
    script = build_install_script(
        camera_id="x",
        server_url="http://x:8080",
        agent_version="0.3.0",
    )
    assert "rpicam-apps-lite" in script
    assert "systemctl daemon-reload" in script
    assert "systemctl enable --now timelapse-agent" in script


def test_script_rejects_dangerous_input():
    import pytest
    with pytest.raises(ValueError):
        build_install_script(
            camera_id="ok",
            server_url="http://ok:8080;rm -rf /",
            agent_version="0.3.0",
        )
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/server/test_provision_script.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `server/app/provision_script.py`**

```python
from __future__ import annotations

import json
import re
from textwrap import dedent

VALID_SERVER_URL_RE = re.compile(r"^https?://[A-Za-z0-9._-]+(?::\d+)?(?:/[A-Za-z0-9._/~-]*)?$")
VALID_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def build_install_script(camera_id: str, server_url: str, agent_version: str) -> str:
    if not VALID_SERVER_URL_RE.match(server_url):
        raise ValueError(f"Invalid server_url: {server_url!r}")
    if not VALID_VERSION_RE.match(agent_version):
        raise ValueError(f"Invalid agent_version: {agent_version!r}")
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$", camera_id):
        raise ValueError(f"Invalid camera_id: {camera_id!r}")

    config_json = json.dumps(
        {
            "camera_id": camera_id,
            "server_url": server_url,
            "config_poll_seconds": 60,
            "work_dir": "/var/lib/timelapse-agent",
        },
        indent=2,
    )

    return dedent(
        """\
        #!/usr/bin/env bash
        set -euo pipefail

        AGENT_VERSION="{agent_version}"
        INSTALL_ROOT=/opt/timelapse-agent
        VERSION_DIR="$INSTALL_ROOT/$AGENT_VERSION"

        sudo apt-get update
        sudo apt-get install -y rpicam-apps-lite python3

        sudo install -d -m 755 "$VERSION_DIR" /etc/timelapse-agent /var/lib/timelapse-agent

        sudo install -m 755 /tmp/timelapse-provision/timelapse_agent.py "$VERSION_DIR/timelapse_agent.py"
        sudo ln -sfn "$VERSION_DIR" "$INSTALL_ROOT/current"

        sudo install -m 644 /tmp/timelapse-provision/timelapse-agent.service /etc/systemd/system/timelapse-agent.service

        sudo tee /etc/timelapse-agent/config.json >/dev/null <<'TIMELAPSE_CONFIG_EOF'
        {config_json}
        TIMELAPSE_CONFIG_EOF
        sudo chmod 644 /etc/timelapse-agent/config.json

        sudo systemctl daemon-reload
        sudo systemctl enable --now timelapse-agent
        """
    ).format(agent_version=agent_version, config_json=config_json)
```

The systemd unit's `ExecStart` path was hardcoded to `/opt/timelapse-agent/timelapse_agent.py` in Phase A. Update it to use the `current` symlink:

In `agent/systemd/timelapse-agent.service`, change:
```
ExecStart=/usr/bin/python3 /opt/timelapse-agent/timelapse_agent.py --config /etc/timelapse-agent/config.json
```
to:
```
ExecStart=/usr/bin/python3 /opt/timelapse-agent/current/timelapse_agent.py --config /etc/timelapse-agent/config.json
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/server/test_provision_script.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add server/app/provision_script.py tests/server/test_provision_script.py agent/systemd/timelapse-agent.service
git commit -m "feat(server): generate Pi install script with input sanitization"
```

---

### Task B5: SSH executor (wave 2, ssh branch)

**Files:**
- Create: `server/app/ssh_provision.py`
- Create: `tests/server/test_ssh_provision.py`

- [ ] **Step 1: Write the failing tests**

`tests/server/test_ssh_provision.py`:
```python
from pathlib import Path
from unittest.mock import patch

import pytest

from app.ssh_provision import (
    ProvisionError,
    ProvisionTarget,
    resolve_target,
    run_provision,
)


def test_resolve_prefers_hostname(monkeypatch):
    def fake_gethostbyname(host: str) -> str:
        if host == "timelapse-x.local":
            return "192.168.1.50"
        raise OSError("not resolved")

    monkeypatch.setattr("app.ssh_provision.socket.gethostbyname", fake_gethostbyname)
    target = resolve_target(expected_hostname="timelapse-x", ip_fallback="10.0.0.99")
    assert target.host == "timelapse-x.local"
    assert target.resolved_ip == "192.168.1.50"


def test_resolve_falls_back_to_ip(monkeypatch):
    def fake_gethostbyname(host: str) -> str:
        raise OSError("nope")

    monkeypatch.setattr("app.ssh_provision.socket.gethostbyname", fake_gethostbyname)
    target = resolve_target(expected_hostname="timelapse-x", ip_fallback="10.0.0.99")
    assert target.host == "10.0.0.99"


def test_resolve_raises_when_no_route(monkeypatch):
    def fake_gethostbyname(host: str) -> str:
        raise OSError("nope")

    monkeypatch.setattr("app.ssh_provision.socket.gethostbyname", fake_gethostbyname)
    with pytest.raises(ProvisionError):
        resolve_target(expected_hostname="x", ip_fallback=None)


def test_run_provision_invokes_expected_subprocess_calls(tmp_path: Path):
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIV", encoding="utf-8")
    private_key.chmod(0o600)

    target = ProvisionTarget(host="timelapse-x.local", resolved_ip="192.168.1.50")
    script = "#!/usr/bin/env bash\necho hi\n"
    payload_files = {
        "timelapse_agent.py": tmp_path / "agent.py",
        "timelapse-agent.service": tmp_path / "service",
    }
    payload_files["timelapse_agent.py"].write_text("print('hi')", encoding="utf-8")
    payload_files["timelapse-agent.service"].write_text("[Unit]", encoding="utf-8")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        from subprocess import CompletedProcess
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("app.ssh_provision.subprocess.run", side_effect=fake_run):
        run_provision(
            target=target,
            ssh_user="pi",
            private_key_path=private_key,
            known_hosts_path=tmp_path / "known_hosts",
            install_script=script,
            payload_files=payload_files,
        )

    flat_args = [" ".join(c) for c in calls]
    assert any("mkdir" in args and "/tmp/timelapse-provision" in args for args in flat_args)
    assert any("scp" in c[0] for c in calls)
    assert any("bash -s" in args for args in flat_args)


def test_run_provision_raises_on_nonzero_exit(tmp_path: Path):
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIV", encoding="utf-8")
    private_key.chmod(0o600)

    target = ProvisionTarget(host="x.local", resolved_ip=None)

    def fake_run(cmd, **kwargs):
        from subprocess import CalledProcessError
        raise CalledProcessError(1, cmd, stderr="boom")

    with patch("app.ssh_provision.subprocess.run", side_effect=fake_run):
        with pytest.raises(ProvisionError, match="boom"):
            run_provision(
                target=target,
                ssh_user="pi",
                private_key_path=private_key,
                known_hosts_path=tmp_path / "known_hosts",
                install_script="echo",
                payload_files={},
            )
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/server/test_ssh_provision.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `server/app/ssh_provision.py`**

```python
from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


class ProvisionError(RuntimeError):
    pass


@dataclass
class ProvisionTarget:
    host: str
    resolved_ip: Optional[str]


def resolve_target(expected_hostname: str, ip_fallback: Optional[str]) -> ProvisionTarget:
    candidates = [f"{expected_hostname}.local"]
    if ip_fallback:
        candidates.append(ip_fallback)
    for candidate in candidates:
        try:
            ip = socket.gethostbyname(candidate)
        except OSError:
            continue
        return ProvisionTarget(host=candidate, resolved_ip=ip)
    raise ProvisionError(
        f"Could not resolve {expected_hostname!r} or fallback IP. "
        "Confirm the Pi finished first boot, joined the LAN, and that the "
        "hostname or IP is correct."
    )


def _ssh_base_args(
    ssh_user: str,
    private_key_path: Path,
    known_hosts_path: Path,
    host: str,
) -> list:
    return [
        "ssh",
        "-i", str(private_key_path),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={known_hosts_path}",
        f"{ssh_user}@{host}",
    ]


def _scp_base_args(
    ssh_user: str,
    private_key_path: Path,
    known_hosts_path: Path,
    host: str,
    sources: list,
    destination: str,
) -> list:
    return [
        "scp",
        "-i", str(private_key_path),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={known_hosts_path}",
        *[str(src) for src in sources],
        f"{ssh_user}@{host}:{destination}",
    ]


def run_provision(
    target: ProvisionTarget,
    ssh_user: str,
    private_key_path: Path,
    known_hosts_path: Path,
    install_script: str,
    payload_files: Dict[str, Path],
) -> None:
    known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
    known_hosts_path.touch(exist_ok=True)

    try:
        # Clean and create staging dir on the Pi.
        subprocess.run(
            _ssh_base_args(ssh_user, private_key_path, known_hosts_path, target.host)
            + ["rm -rf /tmp/timelapse-provision && mkdir -p /tmp/timelapse-provision"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Copy payload files.
        if payload_files:
            subprocess.run(
                _scp_base_args(
                    ssh_user,
                    private_key_path,
                    known_hosts_path,
                    target.host,
                    list(payload_files.values()),
                    "/tmp/timelapse-provision/",
                ),
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )

        # Pipe install script through bash.
        subprocess.run(
            _ssh_base_args(ssh_user, private_key_path, known_hosts_path, target.host)
            + ["bash -s"],
            check=True,
            input=install_script,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.CalledProcessError as error:
        message = error.stderr or error.stdout or str(error)
        raise ProvisionError(message.strip()) from error
    except subprocess.TimeoutExpired as error:
        raise ProvisionError(f"SSH timeout: {error}") from error
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/server/test_ssh_provision.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add server/app/ssh_provision.py tests/server/test_ssh_provision.py
git commit -m "feat(server): SSH executor with hostname/IP fallback resolution"
```

---

### Task B6: `POST /api/agents/{id}/provision` endpoint (wave 3)

**Files:**
- Modify: `server/app/main.py`
- Test: `tests/server/test_provision_endpoint.py`

- [ ] **Step 1: Write the failing tests**

`tests/server/test_provision_endpoint.py`:
```python
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def created_agent(client):
    response = client.post(
        "/api/agents",
        json={
            "agent_id": "tomatoes",
            "display_name": "Tomato",
            "expected_hostname": "timelapse-tomatoes",
            "ip_fallback": "192.168.1.50",
            "ssh_user": "pi",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_provision_marks_agent_provisioned_on_success(client, created_agent, tmp_data_dir: Path):
    with patch("app.main.run_provision") as mock_run, \
         patch("app.main.resolve_target") as mock_resolve:
        from app.ssh_provision import ProvisionTarget
        mock_resolve.return_value = ProvisionTarget(host="timelapse-tomatoes.local", resolved_ip="192.168.1.50")

        response = client.post("/api/agents/tomatoes/provision", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "provisioned"
    assert mock_run.call_count == 1


def test_provision_records_error_on_failure(client, created_agent):
    with patch("app.main.resolve_target") as mock_resolve:
        from app.ssh_provision import ProvisionError
        mock_resolve.side_effect = ProvisionError("could not resolve")

        response = client.post("/api/agents/tomatoes/provision", json={})

    assert response.status_code == 502
    body = response.json()
    assert "could not resolve" in body["detail"]

    follow_up = client.get("/api/agents/tomatoes").json()
    assert follow_up["status"] == "failed"
    assert "could not resolve" in follow_up["last_provision_error"]


def test_provision_missing_agent_returns_404(client):
    response = client.post("/api/agents/nope/provision", json={})
    assert response.status_code == 404


def test_provision_accepts_updated_ip_fallback(client, created_agent):
    with patch("app.main.run_provision"), \
         patch("app.main.resolve_target") as mock_resolve:
        from app.ssh_provision import ProvisionTarget
        mock_resolve.return_value = ProvisionTarget(host="10.0.0.42", resolved_ip="10.0.0.42")

        response = client.post(
            "/api/agents/tomatoes/provision",
            json={"ip_fallback": "10.0.0.42"},
        )

    assert response.status_code == 200
    follow_up = client.get("/api/agents/tomatoes").json()
    assert follow_up["ip_fallback"] == "10.0.0.42"
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/server/test_provision_endpoint.py -v`
Expected: 404 (endpoint missing).

- [ ] **Step 3: Add the endpoint to `server/app/main.py`**

Add the import next to the others:
```python
from app.provision_script import build_install_script
from app.ssh_provision import ProvisionError, resolve_target, run_provision
```

Add a constant near the top:
```python
REPO_ROOT = Path(__file__).resolve().parents[2]
```

Add the request model after `CreateAgentRequest`:
```python
class ProvisionRequest(BaseModel):
    ip_fallback: Optional[str] = None
```

Add the endpoint:
```python
@app.post("/api/agents/{agent_id}/provision")
def provision_agent(agent_id: str, payload: ProvisionRequest, request: Request) -> Dict[str, Any]:
    agent_id = safe_identifier(agent_id)
    store = agent_store()
    try:
        agent = store.get(agent_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Agent not found") from error

    if payload.ip_fallback is not None:
        ip_fallback = validate_ip(payload.ip_fallback)
        agent.ip_fallback = ip_fallback
        store._write(agent)  # type: ignore[attr-defined]

    store.update_status(agent_id, status="provisioning", last_provision_error=None)

    server_url = str(request.base_url).rstrip("/")
    agent_dir = DATA_DIR / "agents" / agent_id
    private_key_path = agent_dir / "id_ed25519"
    known_hosts_path = agent_dir / "known_hosts"

    try:
        target = resolve_target(agent.expected_hostname, agent.ip_fallback)
        from agent.timelapse_agent import AGENT_VERSION  # noqa: WPS433
        install_script = build_install_script(
            camera_id=agent_id,
            server_url=server_url,
            agent_version=AGENT_VERSION,
        )
        payload_files = {
            "timelapse_agent.py": REPO_ROOT / "agent" / "timelapse_agent.py",
            "timelapse-agent.service": REPO_ROOT / "agent" / "systemd" / "timelapse-agent.service",
        }
        run_provision(
            target=target,
            ssh_user=agent.ssh_user,
            private_key_path=private_key_path,
            known_hosts_path=known_hosts_path,
            install_script=install_script,
            payload_files=payload_files,
        )
    except ProvisionError as error:
        store.update_status(agent_id, status="failed", last_provision_error=str(error))
        raise HTTPException(status_code=502, detail=str(error)) from error

    store.update_status(agent_id, status="provisioned", last_provision_error=None)
    return agent_to_response(store.get(agent_id), include_public_key=False)
```

- [ ] **Step 4: Add `agent` package import support**

The provisioning code does `from agent.timelapse_agent import AGENT_VERSION`. For tests, the `pyproject.toml` already adds `agent` to `pythonpath` — verify with:
```bash
grep pythonpath pyproject.toml
```
Expected output includes `"agent"`.

If the `agent/` directory does not have an `__init__.py`, create one:
```bash
test -f agent/__init__.py || : > agent/__init__.py
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/server -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/app/main.py agent/__init__.py tests/server/test_provision_endpoint.py
git commit -m "feat(server): POST /api/agents/{id}/provision orchestrates SSH install"
```

---

### Task B7: Key archival policy (wave 3)

**Files:**
- Modify: `server/app/main.py`
- Modify: `server/app/agents.py`
- Test: extend `tests/server/test_provision_endpoint.py`

After successful check-in by the new agent, the private key must move to `agents/{id}/archive/` and be removed from `agents/{id}/id_ed25519`. We trigger this from the existing checkin handler when the agent is in `provisioned` status.

- [ ] **Step 1: Write the failing test**

Append to `tests/server/test_provision_endpoint.py`:
```python
def test_private_key_archived_after_first_checkin(client, created_agent, tmp_data_dir: Path):
    with patch("app.main.run_provision"), \
         patch("app.main.resolve_target") as mock_resolve:
        from app.ssh_provision import ProvisionTarget
        mock_resolve.return_value = ProvisionTarget(host="x.local", resolved_ip="x")
        client.post("/api/agents/tomatoes/provision", json={})

    private_path = tmp_data_dir / "agents" / "tomatoes" / "id_ed25519"
    assert private_path.exists()

    client.post(
        "/api/cameras/tomatoes/checkin",
        json={"agent_version": "0.3.0"},
    )

    assert not private_path.exists()
    archived = tmp_data_dir / "agents" / "tomatoes" / "archive" / "id_ed25519"
    assert archived.exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/server/test_provision_endpoint.py::test_private_key_archived_after_first_checkin -v`
Expected: FAIL.

- [ ] **Step 3: Add archival helper to `server/app/agents.py`**

Append:
```python
class KeyArchive:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "agents"

    def archive_private_key(self, agent_id: str) -> bool:
        agent_dir = self.root / agent_id
        private_path = agent_dir / "id_ed25519"
        if not private_path.exists():
            return False
        archive_dir = agent_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / "id_ed25519"
        private_path.replace(target)
        target.chmod(0o600)
        return True
```

- [ ] **Step 4: Wire archival into checkin handler**

In `post_checkin`, after `save_store(store)`, add:
```python
    try:
        agent = agent_store().get(camera_id)
    except KeyError:
        agent = None
    if agent is not None and agent.status == "provisioned":
        from app.agents import KeyArchive
        KeyArchive(DATA_DIR).archive_private_key(camera_id)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/server -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/app/agents.py server/app/main.py tests/server/test_provision_endpoint.py
git commit -m "feat(server): archive provisioning private key after first agent check-in"
```

---

### Task B8: Documentation + installer dependency

**Files:**
- Modify: `scripts/install-server.sh`
- Modify: `README.md`

- [ ] **Step 1: Add `openssh-client` to `scripts/install-server.sh`**

In the apt install line, change:
```bash
apt-get install -y ca-certificates curl ffmpeg git iproute2 python3 python3-venv
```
to:
```bash
apt-get install -y ca-certificates curl ffmpeg git iproute2 openssh-client python3 python3-venv
```

- [ ] **Step 2: Add Create-Agent walkthrough to README.md**

Insert a new section under `## Agent Setup` (replace its body):
```markdown
## Agent Setup (Server-Driven)

The server provisions each Raspberry Pi over SSH the first time you connect it. You only use Raspberry Pi Imager to get the Pi online.

### 1. Create a pending agent

```bash
curl -X POST http://<SERVER_IP>:8080/api/agents \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "tomatoes-zero-w",
    "display_name": "Tomato Cam",
    "expected_hostname": "timelapse-tomatoes",
    "ip_fallback": null,
    "ssh_user": "pi"
  }'
```

The response contains a `public_key` line. Copy it.

### 2. Flash the SD card with Raspberry Pi Imager

In Raspberry Pi Imager, choose **Raspberry Pi OS Lite** and open the OS-customisation panel. Set:

- Hostname: the same value you used for `expected_hostname`.
- SSH: enabled, **using the public key** you copied above.
- Wi-Fi SSID, password, and country.

Flash, insert the SD card, power the Pi on, and wait one minute for first boot.

### 3. Provision

```bash
curl -X POST http://<SERVER_IP>:8080/api/agents/tomatoes-zero-w/provision \
  -H 'Content-Type: application/json' \
  -d '{}'
```

If the server cannot reach `timelapse-tomatoes.local`, supply the IP:
```bash
curl -X POST .../provision -H 'Content-Type: application/json' -d '{"ip_fallback":"192.168.1.50"}'
```

After success the agent posts a heartbeat within 60 seconds. List agents:
```bash
curl http://<SERVER_IP>:8080/api/agents
```
```

- [ ] **Step 3: Commit**

```bash
git add scripts/install-server.sh README.md
git commit -m "docs: server-driven SSH provisioning walkthrough"
```

---

## Self-review checklist

- [ ] No paramiko or other Python SSH lib added (we shell out).
- [ ] `safe_identifier` validates all agent_id and camera_id inputs into Phase B endpoints.
- [ ] `validate_hostname`, `validate_ip`, `validate_ssh_user` are called on every CreateAgent payload field that flows into a shell command.
- [ ] `build_install_script` rejects shell-metacharacters in `server_url`, `agent_version`, `camera_id`.
- [ ] Private key chmod 600 in both fresh-keygen and archive paths.
- [ ] LAN-only middleware still applies to `/api/agents/*` (no bypass).
- [ ] After Phase B, the agent install on the Pi uses `/opt/timelapse-agent/current/` symlink — Phase C will rely on this.
