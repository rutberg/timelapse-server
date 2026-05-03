import pytest


def test_capture_hours_null_accepted(client):
    response = client.put(
        "/api/cameras/x/config",
        json={
            "enabled": True, "interval_seconds": 60,
            "image_width": None, "image_height": None,
            "jpeg_quality": 85, "capture_hours": None,
        },
    )
    assert response.status_code == 200
    assert response.json()["capture_hours"] is None


def test_capture_hours_list_stored_sorted(client):
    response = client.put(
        "/api/cameras/x/config",
        json={
            "enabled": True, "interval_seconds": 60,
            "image_width": None, "image_height": None,
            "jpeg_quality": 85, "capture_hours": [17, 9, 12, 9],
        },
    )
    # Duplicate (9) rejected.
    assert response.status_code == 422


def test_capture_hours_dedup_sorted_when_unique(client):
    response = client.put(
        "/api/cameras/x/config",
        json={
            "enabled": True, "interval_seconds": 60,
            "image_width": None, "image_height": None,
            "jpeg_quality": 85, "capture_hours": [17, 9, 12],
        },
    )
    assert response.status_code == 200
    assert response.json()["capture_hours"] == [9, 12, 17]


def test_capture_hours_out_of_range_rejected(client):
    response = client.put(
        "/api/cameras/x/config",
        json={
            "enabled": True, "interval_seconds": 60,
            "image_width": None, "image_height": None,
            "jpeg_quality": 85, "capture_hours": [24],
        },
    )
    assert response.status_code == 422


def test_capture_hours_empty_list_rejected(client):
    response = client.put(
        "/api/cameras/x/config",
        json={
            "enabled": True, "interval_seconds": 60,
            "image_width": None, "image_height": None,
            "jpeg_quality": 85, "capture_hours": [],
        },
    )
    assert response.status_code == 422
