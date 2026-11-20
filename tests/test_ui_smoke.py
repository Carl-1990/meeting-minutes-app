import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance() or QApplication([])
    yield app


def test_modules_import_and_window_builds(qapp):
    import main

    assert callable(main.main)
    window = MainWindow()
    window.show()
    qapp.processEvents()

    assert window.windowTitle() == "会议纪要"
    assert window.sidebar.start_button.text() == "开始会议"
    assert window.sidebar.pause_button.text() == "暂停会议"
    assert window.sidebar.end_button.text() == "结束会议"
    assert window.sidebar.auto_radio.isChecked()
    assert len(window.sidebar.speaker_buttons) == 10
    assert window.sidebar.speaker_buttons[1].text() == "发言人1"
    assert window.sidebar.speaker_buttons[10].full_name == "发言人10"
    assert not window.sidebar.pause_button.isEnabled()
    assert not window.sidebar.end_button.isEnabled()

    window.session.add_segment(1, 2, 1, "你好", "manual")
    window.transcript.reload()
    qapp.processEvents()
    assert "你好" in window.transcript.plain_text()

    window.rename_speaker(1, "张三")
    qapp.processEvents()
    assert "张三" in window.sidebar.speaker_buttons[1].toolTip()
    assert "张三" in window.transcript.plain_text()
    assert "发言人1" not in window.transcript.plain_text()

    window.reassign_segment(window.session.segments[0].idx, 2)
    assert window.session.segments[0].source == "corrected"
    assert "发言人2：你好" in window.transcript.plain_text()

    window.session.start(mono=0)
    window._mic_ready = True
    window.refresh_buttons()
    assert window.sidebar.start_button.text() == "会议进行中"
    assert window.sidebar.pause_button.isEnabled()
    assert window.sidebar.end_button.isEnabled()
    assert window.sidebar.start_button.property("phase") == "recording"

    window.session.pause(mono=3)
    window.refresh_buttons()
    assert window.sidebar.pause_button.text() == "继续会议"
    assert window.sidebar.start_button.property("phase") == "paused"

    window.session.begin_export(mono=3, duration=3)
    window.session.finish_export()
    window.close()
    qapp.processEvents()
