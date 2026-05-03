def test_camera_config_accepts_desired_agent_version(client):
    response = client.put(
        "/api/cameras/tomatoes/config",
        json={
            "enabled": True,
            "interval_seconds": 600,
            "image_width": None,
            "image_height": None,
            "jpeg_quality": 85,
            "desired_agent_version": "0.3.1",
        },
    )
    assert response.status_code == 200
    assert response.json()["desired_agent_version"] == "0.3.1"


def test_manifest_404_when_no_desired_version(client):
    response = client.get("/api/cameras/tomatoes/update-manifest")
    assert response.status_code == 404


def test_manifest_returns_url_and_sha_when_release_present(client, tmp_data_dir):
    releases = tmp_data_dir / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    bundle = releases / "timelapse-agent-0.3.1.tar.gz"
    bundle.write_bytes(b"fake-tarball")
    sha = releases / "timelapse-agent-0.3.1.tar.gz.sha256"
    sha.write_text("deadbeef\n", encoding="utf-8")

    client.put(
        "/api/cameras/tomatoes/config",
        json={
            "enabled": True,
            "interval_seconds": 600,
            "image_width": None,
            "image_height": None,
            "jpeg_quality": 85,
            "desired_agent_version": "0.3.1",
        },
    )

    response = client.get("/api/cameras/tomatoes/update-manifest")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "0.3.1"
    assert body["url"].endswith("/api/releases/timelapse-agent-0.3.1.tar.gz")
    assert body["sha256"] == "deadbeef"


def test_manifest_503_when_release_missing(client):
    client.put(
        "/api/cameras/tomatoes/config",
        json={
            "enabled": True,
            "interval_seconds": 600,
            "image_width": None,
            "image_height": None,
            "jpeg_quality": 85,
            "desired_agent_version": "9.9.9",
        },
    )
    response = client.get("/api/cameras/tomatoes/update-manifest")
    assert response.status_code == 503
