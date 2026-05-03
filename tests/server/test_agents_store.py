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
