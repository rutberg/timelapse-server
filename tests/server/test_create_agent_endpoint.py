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
