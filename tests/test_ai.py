import json
from types import SimpleNamespace

import pytest

from app import ai, server


def test_json_is_found_inside_fences_and_chatter():
    assert ai.parse_json('Sure!\n```json\n{"clips": [1]}\n```') == {"clips": [1]}
    with pytest.raises(ai.AIError):
        ai.parse_json("no json here")


class FakeClient:
    """Stands in for openai.OpenAI; remembers what it was asked."""
    def __init__(self, reply="{}", models=()):
        self.calls, self._reply, self._models = [], reply, models
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(list=lambda **_: [SimpleNamespace(to_dict=lambda m=m: m) for m in self._models])

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._reply))])


@pytest.fixture
def settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ai, "SETTINGS_FILE", path)
    monkeypatch.setattr(server, "SETTINGS_FILE", path)
    return path


def test_ask_ai_uses_the_picked_provider_model_and_its_key(settings, monkeypatch):
    settings.write_text(json.dumps({"ai": {"provider": "groq", "model": "llama-x", "base_url": ai.PROVIDERS["groq"]["base_url"],
                                           "keys": {ai.PROVIDERS["groq"]["base_url"]: "gsk_1", "https://other/v1": "nope"}}}))
    fake, seen = FakeClient('{"ok": true}'), {}
    monkeypatch.setattr(ai, "_client", lambda provider, url, key, timeout: seen.update(url=url, key=key) or fake)
    assert ai.ask_ai("prompt", {"type": "object"}, "system") == {"ok": True}
    assert seen == {"url": ai.PROVIDERS["groq"]["base_url"], "key": "gsk_1"}
    assert fake.calls[0]["model"] == "llama-x"


def test_ask_ai_defaults_to_claude_code(settings, monkeypatch):
    monkeypatch.setattr(ai, "ask_claude", lambda p, s, sy, model: {"model": model})
    assert ai.ask_ai("p", {}, "s") == {"model": "sonnet"}


def test_key_is_saved_per_address_and_never_sent_back(settings, monkeypatch):
    from fastapi.testclient import TestClient
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")
    keys_used = []
    monkeypatch.setattr(ai, "_client", lambda p, url, key, t: keys_used.append((url, key)) or FakeClient(models=[{"id": "b"}, {"id": "a", "context_length": 8}]))

    saved = client.put("/api/ai", json={"provider": "openrouter", "api_key": "sk-secret", "model": "a"}).json()
    assert "sk-secret" not in json.dumps(saved) and saved["key_urls"] == [ai.PROVIDERS["openrouter"]["base_url"]]

    models = client.post("/api/ai/models", json={"provider": "openrouter"}).json()
    assert [m["id"] for m in models] == ["a", "b"] and models[0]["context_length"] == 8
    client.post("/api/ai/models", json={"provider": "custom", "base_url": "https://elsewhere.example/v1"})
    assert keys_used == [(ai.PROVIDERS["openrouter"]["base_url"], "sk-secret"), ("https://elsewhere.example/v1", "")]


def test_bad_provider_or_address_is_refused(settings):
    from fastapi.testclient import TestClient
    client = TestClient(server.app, base_url="http://127.0.0.1:8765")
    assert client.put("/api/ai", json={"provider": "nope", "model": "x"}).status_code == 422
    assert client.post("/api/ai/models", json={"provider": "custom", "base_url": "file:///etc/passwd"}).status_code == 422


def test_provider_error_text_survives_a_list_wrapped_body():
    error = SimpleNamespace(body=[{"error": {"message": "Model retired, use gemini-3.8-flash"}}], message="Error code: 404")
    assert ai._short(error) == "Model retired, use gemini-3.8-flash"
