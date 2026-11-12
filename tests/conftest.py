import os
import sys

# Make the project root importable (agents/, apis/, video/, config, gui)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STUDIO_NO_BROWSER", "1")
os.environ.setdefault("STUDIO_SCHEDULER", "0")

import pathlib

import pytest


@pytest.fixture(autouse=True)
def skip_when_symlinks_need_privilege(monkeypatch):
    """Windows only lets elevated shells or Developer Mode create symlinks.

    The link-escape tests cannot build their fixture without that privilege;
    report them as skipped there rather than as product failures.
    """
    if os.name != "nt":
        return
    original = pathlib.Path.symlink_to

    def symlink_to(self, target, target_is_directory=False):
        try:
            return original(self, target, target_is_directory)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Creating symbolic links needs Developer Mode or an elevated shell on Windows.")
            raise
    monkeypatch.setattr(pathlib.Path, "symlink_to", symlink_to)


@pytest.fixture(autouse=True)
def isolated_channel_storage(tmp_path, monkeypatch):
    import config
    from uploader import youtube
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / ".yaas"))
    monkeypatch.setattr(youtube, "TOKEN_FILE", str(tmp_path / "youtube_token.json"))
