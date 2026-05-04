def test_invalid_id_is_rejected(client):
    response = client.get("/api/cameras/has space/config")
    assert response.status_code == 400
    assert "camera id" in response.json()["detail"].lower()


def test_valid_id_is_accepted(client):
    response = client.get("/api/cameras/timelapse-tomatoes_01.cam/config")
    assert response.status_code == 200


def test_empty_id_is_rejected(client):
    response = client.get("/api/cameras/-/config")
    assert response.status_code == 400


def test_id_with_slash_is_rejected_via_checkin(client):
    # Slashes (encoded or not) must never reach a handler with a
    # path-traversal id. httpx normalizes %2F to / before sending,
    # which makes the route not match (404). Either 400 or 404 is
    # acceptable: the request is rejected without persisting.
    response = client.post(
        "/api/cameras/path%2Ftraversal/checkin",
        json={"agent_version": "0.0.1"},
    )
    assert response.status_code in (400, 404)
