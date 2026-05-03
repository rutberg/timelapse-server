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
