from datetime import datetime

import pytest

from app.core.session import MeetingSession, MeetingState, format_timestamp


def test_timestamp_format():
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(3.9) == "00:00:03"
    assert format_timestamp(195) == "00:03:15"
    assert format_timestamp(3661) == "01:01:01"
    assert format_timestamp(-4) == "00:00:00"


def test_state_machine_and_pause_is_excluded_from_duration():
    session = MeetingSession()
    assert session.state == MeetingState.IDLE
    assert session.can_start()
    assert not session.can_pause()
    assert not session.can_resume()
    assert not session.can_end()

    with pytest.raises(ValueError):
        session.pause()
    with pytest.raises(ValueError):
        session.begin_export()

    session.start(now=datetime(2026, 1, 1, 10, 0, 0), mono=0)
    assert session.state == MeetingState.RECORDING
    assert session.can_pause() and session.can_end() and not session.can_start()

    session.pause(mono=10)
    assert session.state == MeetingState.PAUSED
    assert session.can_resume() and not session.can_pause()
    assert session.active_duration(50) == pytest.approx(10)

    session.resume(mono=50)
    assert session.active_duration(55) == pytest.approx(15)
    session.begin_export(now=datetime(2026, 1, 1, 10, 5, 0), mono=55)
    assert session.state == MeetingState.EXPORTING
    assert session.duration_seconds == pytest.approx(15)
    assert session.ended_at == datetime(2026, 1, 1, 10, 5, 0)
    assert not session.can_end()

    session.finish_export()
    assert session.state == MeetingState.IDLE
    assert session.can_start()
    with pytest.raises(ValueError):
        session.finish_export()


def test_finish_export_requires_exporting_state():
    session = MeetingSession()
    with pytest.raises(ValueError):
        session.finish_export()


def test_rename_propagates_to_every_past_line():
    session = MeetingSession()
    session.start(mono=0)
    session.add_segment(0, 1.2, 1, "第一句", "auto")
    session.add_segment(2, 3.4, 1, "第二句", "manual")
    session.add_segment(4, 5, 2, "别人的话", "auto")

    session.rename_speaker(1, "张三")
    assert session.format_line(session.segments[0]) == "[00:00:00] 张三：第一句"
    assert session.format_line(session.segments[1]) == "[00:00:02] 张三：第二句"
    assert session.format_line(session.segments[2]) == "[00:00:04] 发言人2：别人的话"

    session.rename_speaker(1, "李四")
    assert session.segments[0].speaker_id == 1
    assert session.format_line(session.segments[0]) == "[00:00:00] 李四：第一句"
    assert "李四" in session.participant_names()
    assert session.participant_names() == ["李四", "发言人2"]


def test_rename_rejects_blank_name():
    session = MeetingSession()
    with pytest.raises(ValueError):
        session.rename_speaker(1, "   ")
    assert session.speakers[1].name == "发言人1"


def test_reassign_marks_segment_corrected():
    session = MeetingSession()
    session.start(mono=0)
    segment = session.add_segment(8, 9, 1, "这句话", "auto")
    updated = session.reassign_segment(segment.idx, 4)
    assert updated.speaker_id == 4
    assert updated.source == "corrected"
    assert session.format_line(updated) == "[00:00:08] 发言人4：这句话"
    with pytest.raises(KeyError):
        session.reassign_segment(999, 1)
    with pytest.raises(ValueError):
        session.reassign_segment(segment.idx, 11)


def test_new_meeting_clears_previous_segments_unless_resuming():
    session = MeetingSession()
    session.start(mono=0)
    session.add_segment(0, 5, 1, "继续", "auto")
    session.begin_export(mono=5, duration=5)
    session.finish_export()

    session.start(mono=100, keep_existing=True)
    assert len(session.segments) == 1
    assert session.active_duration(100) == pytest.approx(5)
    assert session.active_duration(110) == pytest.approx(15)

    session.begin_export(mono=110, duration=15)
    session.finish_export()
    session.start(mono=0)
    assert session.segments == []
    assert session.speakers[1].name == "发言人1"


def test_ten_speakers_exist_by_default():
    session = MeetingSession()
    assert list(session.speakers) == list(range(1, 11))
    assert session.speakers[10].name == "发言人10"
