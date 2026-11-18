import pytest

from app import sfx


@pytest.fixture(autouse=True)
def sfx_library(tmp_path_factory, monkeypatch):
    """Tests never touch the real data/sfx; the built-in sounds are made once per session."""
    folder = tmp_path_factory.getbasetemp() / "sfx"
    monkeypatch.setattr(sfx, "SFX_DIR", folder)
    monkeypatch.setattr(sfx, "INDEX", folder / "sounds.json")


@pytest.fixture(autouse=True)
def no_ai_edit_pass(monkeypatch):
    """Adding clips never calls the real AI in tests (tests of app.edits call it with a fake)."""
    from app import pipeline
    monkeypatch.setattr(pipeline, "decorate", lambda video, clips, ask, log=None: clips)
