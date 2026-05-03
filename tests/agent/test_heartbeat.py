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
    assert captured["body"]["pending_count"] == 0
    assert captured["body"]["pending_bytes"] == 0


def test_post_checkin_includes_pending_counts():
    settings = {"camera_id": "tomatoes", "server_url": "http://server.local:8080"}
    state = agent.AgentState(pending_count=12, pending_bytes=4_500_000)

    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        response = MagicMock()
        response.read.return_value = b'{"acknowledged": true}'
        response.__enter__ = lambda self: self
        response.__exit__ = lambda self, *args: None
        return response

    with patch.object(agent, "urlopen", side_effect=fake_urlopen):
        agent.post_checkin(settings, state)

    assert captured["body"]["pending_count"] == 12
    assert captured["body"]["pending_bytes"] == 4_500_000


def test_measure_pending_counts_jpgs_only(tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "a.jpg").write_bytes(b"x" * 100)
    (pending / "b.jpg").write_bytes(b"x" * 250)
    (pending / "c.json").write_bytes(b"{}")  # sidecar metadata, ignored

    count, total = agent.measure_pending(tmp_path)
    assert count == 2
    assert total == 350


def test_measure_pending_returns_zero_when_dir_missing(tmp_path):
    count, total = agent.measure_pending(tmp_path)
    assert count == 0
    assert total == 0


def test_evict_pending_drops_oldest_until_under_cap(tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    # Lexical sort = chronological with our UTC timestamp filename scheme.
    for index, name in enumerate(["20260501T000000Z.jpg", "20260502T000000Z.jpg", "20260503T000000Z.jpg"]):
        (pending / name).write_bytes(b"x" * 200)
        (pending / name.replace(".jpg", ".json")).write_text("{}")

    evicted_count, evicted_bytes = agent.evict_pending(tmp_path, max_bytes=400)

    assert evicted_count == 1
    assert evicted_bytes == 200
    remaining = sorted(p.name for p in pending.glob("*.jpg"))
    assert remaining == ["20260502T000000Z.jpg", "20260503T000000Z.jpg"]
    # Sidecar of evicted file is also gone.
    assert not (pending / "20260501T000000Z.json").exists()
    assert (pending / "20260502T000000Z.json").exists()


def test_evict_pending_noop_when_under_cap(tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "a.jpg").write_bytes(b"x" * 100)

    evicted_count, evicted_bytes = agent.evict_pending(tmp_path, max_bytes=1000)

    assert (evicted_count, evicted_bytes) == (0, 0)
    assert (pending / "a.jpg").exists()


def test_resolve_max_pending_bytes_explicit_value(tmp_path):
    settings = {"max_pending_bytes": 1234}
    assert agent.resolve_max_pending_bytes(settings, tmp_path) == 1234


def test_resolve_max_pending_bytes_zero_means_unlimited(tmp_path):
    settings = {"max_pending_bytes": 0}
    assert agent.resolve_max_pending_bytes(settings, tmp_path) == 0


def test_resolve_max_pending_bytes_auto_when_missing(tmp_path, monkeypatch):
    # Pretend the partition is 16 GB total.
    fake_usage = type("U", (), {"total": 16 * 1024 * 1024 * 1024, "used": 0, "free": 0})()
    monkeypatch.setattr(agent.shutil, "disk_usage", lambda p: fake_usage)

    result = agent.resolve_max_pending_bytes({}, tmp_path)
    assert result == 16 * 1024 * 1024 * 1024 // 2  # 50%


def test_resolve_max_pending_bytes_falls_back_when_disk_query_fails(tmp_path, monkeypatch):
    def boom(_):
        raise OSError("denied")
    monkeypatch.setattr(agent.shutil, "disk_usage", boom)

    assert agent.resolve_max_pending_bytes({}, tmp_path) == agent.FALLBACK_MAX_PENDING_BYTES


def test_hour_in_schedule_no_schedule_means_always():
    from datetime import datetime
    assert agent.hour_in_schedule(datetime(2026, 5, 3, 14), None) is True
    assert agent.hour_in_schedule(datetime(2026, 5, 3, 14), []) is True


def test_hour_in_schedule_only_during_listed_hours():
    from datetime import datetime
    schedule = [9, 10, 11, 12, 13, 14, 15, 16, 17]
    assert agent.hour_in_schedule(datetime(2026, 5, 3, 9, 0), schedule) is True
    assert agent.hour_in_schedule(datetime(2026, 5, 3, 17, 59), schedule) is True
    assert agent.hour_in_schedule(datetime(2026, 5, 3, 8, 59), schedule) is False
    assert agent.hour_in_schedule(datetime(2026, 5, 3, 18, 0), schedule) is False
    assert agent.hour_in_schedule(datetime(2026, 5, 3, 23, 0), schedule) is False


def test_evict_pending_disabled_when_max_bytes_zero(tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "a.jpg").write_bytes(b"x" * 1000)

    evicted_count, _ = agent.evict_pending(tmp_path, max_bytes=0)

    assert evicted_count == 0
    assert (pending / "a.jpg").exists()


def test_post_checkin_swallows_network_errors():
    settings = {"camera_id": "tomatoes", "server_url": "http://nope:8080"}
    state = agent.AgentState()

    def fake_urlopen(*args, **kwargs):
        raise agent.URLError("nope")

    with patch.object(agent, "urlopen", side_effect=fake_urlopen):
        agent.post_checkin(settings, state)
