def test_serves_existing_release(client, tmp_data_dir):
    releases = tmp_data_dir / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    (releases / "timelapse-agent-0.3.1.tar.gz").write_bytes(b"binary-bytes")

    response = client.get("/api/releases/timelapse-agent-0.3.1.tar.gz")
    assert response.status_code == 200
    assert response.content == b"binary-bytes"
    assert response.headers["content-type"] == "application/gzip"


def test_release_404_when_missing(client):
    response = client.get("/api/releases/timelapse-agent-9.9.9.tar.gz")
    assert response.status_code == 404


def test_release_400_for_path_traversal(client):
    response = client.get("/api/releases/..%2Fconfig.json")
    assert response.status_code in (400, 404)
