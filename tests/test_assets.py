import io
import subprocess

import pytest
from PIL import Image

from app import FFMPEG, assets


@pytest.fixture(autouse=True)
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "MUSIC_DIR", tmp_path / "music")
    monkeypatch.setattr(assets, "LOGO_FILE", tmp_path / "logo.png")


def tone(path, seconds=2):
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}", str(path)], check=True)
    return path


def test_music_is_saved_under_a_safe_name_and_listed_with_its_length(tmp_path):
    name = assets.save_music("../../My Song (final).mp3", tone(tmp_path / "in.mp3").open("rb"))
    assert name == "My-Song-final.mp3"
    assert [(m["name"], round(m["duration"])) for m in assets.list_music()] == [("My-Song-final.mp3", 2)]
    assert assets.music_path(name).is_file()


def test_music_with_a_bad_type_or_name_is_refused(tmp_path):
    with pytest.raises(ValueError):
        assets.save_music("evil.sh", io.BytesIO(b"#!/bin/sh"))
    with pytest.raises(ValueError):
        assets.save_music("fake.mp3", io.BytesIO(b"not audio"))
    with pytest.raises(KeyError):
        assets.music_path("../settings.json")


def test_logo_is_re_encoded_as_a_small_png_and_reports_its_size():
    big = io.BytesIO()
    Image.new("RGBA", (2000, 1000), (255, 0, 0, 128)).save(big, "PNG")
    big.seek(0)
    assets.save_logo(big)
    assert assets.logo_size() == (512, 256)
    with pytest.raises(ValueError):
        assets.save_logo(io.BytesIO(b"<svg onload=alert(1)>"))
