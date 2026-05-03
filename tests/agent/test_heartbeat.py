import json
from unittest.mock import MagicMock, patch

import timelapse_agent as agent


def test_post_checkin_sends_expected_payload():
    settings = {
        "camera_id": "tomatoes",
        "server_url": "http://server.local:8080",
    }
    state = agent.AgentState(
        last_capture_at="2026-05-03T12:00:00Z",
        last_upload_at="2026-05-03T12:00:05Z",
        last_error=None,
    )

    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = dict(request.headers)
        response = MagicMock()
        response.read.return_value = b'{"acknowledged": true}'
        response.__enter__ = lambda self: self
        response.__exit__ = lambda self, *args: None
        return response

    with patch.object(agent, "urlopen", side_effect=fake_urlopen):
        agent.post_checkin(settings, state)

    assert captured["url"] == "http://server.local:8080/api/cameras/tomatoes/checkin"
    assert captured["body"]["agent_version"] == agent.AGENT_VERSION
    assert captured["body"]["last_capture_at"] == "2026-05-03T12:00:00Z"
    assert captured["body"]["last_upload_at"] == "2026-05-03T12:00:05Z"
    assert captured["body"]["last_error"] is None
    assert captured["body"]["hostname"]


def test_post_checkin_swallows_network_errors():
    settings = {"camera_id": "tomatoes", "server_url": "http://nope:8080"}
    state = agent.AgentState()

    def fake_urlopen(*args, **kwargs):
        raise agent.URLError("nope")

    with patch.object(agent, "urlopen", side_effect=fake_urlopen):
        agent.post_checkin(settings, state)
