from datetime import datetime
from pathlib import Path

import numpy as np
from docx import Document

from app.core.audio_capture import WavSink
from app.core.session import MeetingSession
from app.export.docx_exporter import export_meeting


def _session() -> MeetingSession:
    session = MeetingSession()
    session.start(now=datetime(2026, 5, 1, 14, 30, 0), mono=0)
    session.add_segment(3, 5, 1, "大家好", "manual")
    session.rename_speaker(1, "张三")
    session.add_segment(12, 15, 2, "收到", "auto")
    session.begin_export(now=datetime(2026, 5, 1, 15, 0, 0), mono=1800, duration=1800)
    return session


def test_docx_contains_timestamps_renamed_speakers_and_avoids_collisions(tmp_path):
    session = _session()
    result = export_meeting(session, output_dir=tmp_path)
    assert result.docx_path.name == "会议纪要_20260501_1500.docx"
    assert result.docx_path.parent == tmp_path

    document = Document(str(result.docx_path))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "会议逐字稿" in text
    assert "日期：2026-05-01" in text
    assert "开始时间：14:30:00" in text
    assert "结束时间：15:00:00" in text
    assert "时长：00:30:00" in text
    assert "参会发言人：张三、发言人2" in text
    assert "[00:00:03] 张三：大家好" in text
    assert "[00:00:12] 发言人2：收到" in text

    speaker_runs = [
        run
        for paragraph in document.paragraphs
        for run in paragraph.runs
        if run.text.startswith("张三：")
    ]
    assert speaker_runs
    assert all(run.bold for run in speaker_runs)

    second = export_meeting(session, output_dir=tmp_path)
    assert second.docx_path.name == "会议纪要_20260501_1500_2.docx"


def test_recording_is_saved_beside_the_docx(tmp_path):
    session = _session()
    wav = tmp_path / "capture.wav"
    sink = WavSink(wav)
    sink.write(np.arange(1600, dtype=np.int16))
    sink.close()
    desktop = tmp_path / "desktop"
    result = export_meeting(session, output_dir=desktop, audio_source=wav)
    assert result.audio_path is not None
    assert result.audio_path.suffix in {".wav", ".m4a"}
    assert result.audio_path.stem == "会议纪要_20260501_1500"
    assert result.audio_path.stat().st_size > 0
    if result.audio_path.suffix == ".wav":
        assert result.audio_path.read_bytes() == wav.read_bytes()
    assert result.docx_path == desktop / "会议纪要_20260501_1500.docx"


def test_allocate_skips_an_existing_audio_name(tmp_path: Path):
    session = _session()
    (tmp_path / "会议纪要_20260501_1500.docx").write_bytes(b"placeholder")
    (tmp_path / "会议纪要_20260501_1500_2.wav").write_bytes(b"RIFFxxxx")
    result = export_meeting(session, output_dir=tmp_path)
    assert result.docx_path.name == "会议纪要_20260501_1500_3.docx"
