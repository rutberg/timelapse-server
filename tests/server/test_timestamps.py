import io


def upload_with_ts(client, ts: str):
    return client.post(
        "/api/cameras/tomatoes/upload",
        headers={"X-Captured-At": ts},
        files={"image": ("frame.jpg", io.BytesIO(b"\xff\xd8\xff\xe0jpegbytes"), "image/jpeg")},
    )


def test_upload_with_offset_uses_local_date_in_path(client):
    # 07:35 CEST is the SAME instant as 05:35 UTC, but the file should
    # land in the camera's local-date folder.
    response = upload_with_ts(client, "2026-05-04T07:35:00+02:00")
    assert response.status_code == 200
    body = response.json()
    assert "tomatoes/2026-05-04/" in body["path"]


def test_upload_filename_drops_z_suffix(client):
    response = upload_with_ts(client, "2026-05-04T07:35:00+02:00")
    body = response.json()
    # Local time, no UTC marker.
    assert "20260504T073500" in body["path"]
    assert "Z.jpg" not in body["path"]


def test_upload_at_local_midnight_lands_on_local_date(client):
    # 00:30 CEST on 2026-05-04 is 22:30 UTC on 2026-05-03. Storage must
    # follow the camera's calendar day.
    response = upload_with_ts(client, "2026-05-04T00:30:00+02:00")
    body = response.json()
    assert "tomatoes/2026-05-04/" in body["path"]
    assert "20260504T003000" in body["path"]


def test_upload_with_z_header_still_works(client):
    # Backwards compat: a UTC "Z" header is valid ISO and used as-is.
    response = upload_with_ts(client, "2026-05-04T05:35:00Z")
    body = response.json()
    assert "tomatoes/2026-05-04/" in body["path"]
    assert "20260504T053500" in body["path"]
