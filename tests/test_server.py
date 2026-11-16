import json
import stat

import pytest
from fastapi.testclient import TestClient

from app import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "SETTINGS_FILE", tmp_path / "settings.json")
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=arj7oStGLkU",
    "https://youtu.be/arj7oStGLkU",
    "https://m.youtube.com/watch?v=arj7oStGLkU",
    "http://youtube.com/shorts/abc123",
])
def test_youtube_links_are_accepted(url):
    assert server.is_youtube_url(url)


@pytest.mark.parametrize("url", [
    "https://evil.example/watch?v=youtube.com",
    "https://youtube.com.evil.example/watch?v=1",
    "ftp://www.youtube.com/watch?v=1",
    "file:///etc/passwd",
    "not a url",
    "",
])
def test_other_links_are_rejected(url):
    assert not server.is_youtube_url(url)


def test_find_rejects_a_non_youtube_link(client):
    response = client.post("/api/highlights", json={"url": "https://vimeo.com/123"})

    assert response.status_code == 422


def test_find_rejects_minimum_length_above_maximum(client):
    response = client.post("/api/highlights", json={
        "url": "https://youtu.be/arj7oStGLkU", "min_seconds": 60, "max_seconds": 30,
    })

    assert response.status_code == 422


def test_find_without_ai_settings_explains_what_is_missing(client):
    response = client.post("/api/highlights", json={"url": "https://youtu.be/arj7oStGLkU"})

    assert response.status_code == 400
    assert "API key" in response.json()["detail"]


@pytest.mark.parametrize("session_id", ["..", "..%2F..%2Fetc", "20261116_1500", "abc", "20261116_150000x"])
def test_session_id_cannot_escape_the_data_folder(client, session_id):
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert client.get(f"/api/sessions/{session_id}/clips/0").status_code == 404


def test_unknown_session_and_clip_are_not_found(client):
    assert client.get("/api/sessions/20260101_000000").status_code == 404
    assert client.get("/api/sessions/20260101_000000/clips/0").status_code == 404


def test_api_key_is_stored_privately_and_never_returned(client, tmp_path):
    # Act
    saved = client.put("/api/settings", json={
        "base_url": "https://api.example.com/v1", "model": "some-model", "api_key": "sk-secret-1234",
    })
    shown = client.get("/api/settings").json()

    # Assert
    assert saved.status_code == 200
    assert "sk-secret-1234" not in json.dumps(shown)
    assert shown["api_key_set"] is True
    assert shown["api_key_hint"] == "1234"
    mode = stat.S_IMODE((tmp_path / "settings.json").stat().st_mode)
    assert mode == 0o600


def test_saving_settings_without_a_key_keeps_the_stored_key(client, tmp_path):
    client.put("/api/settings", json={
        "base_url": "https://api.example.com/v1", "model": "a", "api_key": "sk-secret-1234",
    })

    client.put("/api/settings", json={"base_url": "https://api.example.com/v1", "model": "b"})

    stored = json.loads((tmp_path / "settings.json").read_text())
    assert stored["api_key"] == "sk-secret-1234"
    assert stored["model"] == "b"


def test_settings_reject_a_non_http_endpoint(client):
    response = client.put("/api/settings", json={"base_url": "file:///tmp/x", "model": "a"})

    assert response.status_code == 422


def test_requests_for_another_host_are_refused(client):
    # A page on another site resolving its own name to 127.0.0.1 (DNS rebinding)
    response = client.get("/api/settings", headers={"host": "evil.example"})

    assert response.status_code == 403


def test_requests_from_another_site_are_refused(client):
    response = client.put(
        "/api/settings",
        json={"base_url": "https://evil.example/v1", "model": "x"},
        headers={"origin": "https://evil.example"},
    )

    assert response.status_code == 403
