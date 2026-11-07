import os
import sys

# Make the project root importable (agents/, apis/, video/, config, gui)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STUDIO_NO_BROWSER", "1")
os.environ.setdefault("STUDIO_SCHEDULER", "0")

import pytest


@pytest.fixture(autouse=True)
def isolated_channel_storage(tmp_path, monkeypatch):
    import config
    from uploader import youtube
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / ".yaas"))
    monkeypatch.setattr(youtube, "TOKEN_FILE", str(tmp_path / "youtube_token.json"))
