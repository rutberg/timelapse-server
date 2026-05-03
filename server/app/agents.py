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
