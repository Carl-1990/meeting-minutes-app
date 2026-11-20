import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

import pytest


@pytest.fixture(autouse=True)
def isolated_app_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_MINUTES_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEETING_MINUTES_DESKTOP", str(tmp_path / "desktop"))
