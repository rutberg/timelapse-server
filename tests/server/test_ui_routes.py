def test_root_serves_html_shell(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "<title>Timelapse" in body
    assert "/static/app.js" in body
    assert "x-data" in body


def test_static_assets_served(client):
    response = client.get("/static/vendor/alpine.min.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_unknown_route_falls_through_to_shell(client):
    response = client.get("/agents/new")
    assert response.status_code == 200
    assert "<title>Timelapse" in response.text
