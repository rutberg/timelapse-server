def test_root_serves_html_shell(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "<title>Timelapse" in body
    assert 'aside class="sidebar"' in body
    assert 'id="app-root"' in body


def test_static_assets_served(client):
    response = client.get("/static/v2/styles.css")
    assert response.status_code == 200
    assert "css" in response.headers["content-type"]


def test_unknown_route_falls_through_to_shell(client):
    response = client.get("/agents/new")
    assert response.status_code == 200
    assert "<title>Timelapse" in response.text


def test_view_modules_served(client):
    for module in ("dashboard.js", "create-agent.js", "camera.js"):
        response = client.get(f"/static/v2/views/{module}")
        assert response.status_code == 200, module
        assert "registerView" in response.text


def test_app_js_served(client):
    response = client.get("/static/v2/app.js")
    assert response.status_code == 200
    assert "hashchange" in response.text
