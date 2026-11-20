from datetime import datetime

import numpy as np

from app.core.session import MeetingSession, MeetingState
from app.utils.autosave import SessionStore, load_session


def test_autosave_roundtrip_and_incomplete_detection(tmp_path):
    store = SessionStore(tmp_path)
    session = MeetingSession()
    session.mode = "auto"
    session.threshold = 0.62
    session.start(now=datetime(2026, 5, 1, 9, 0, 0), mono=0)
    session.rename_speaker(1, "张三")
    path = store.begin(session)
    store.append_rename(1, "张三")
    session.speakers[1].centroid = np.array([1.0, 0.0, 0.0])
    session.speakers[1].count = 1
    segment = session.add_segment(1.5, 2.5, 1, "你好", "auto", embedding=[1.0, 0.0, 0.0])
    store.append_segment(session, segment)

    assert path in store.find_incomplete()
    loaded = load_session(path)
    assert loaded.state == MeetingState.IDLE
    assert loaded.started_at == datetime(2026, 5, 1, 9, 0, 0)
    assert loaded.speakers[1].name == "张三"
    assert loaded.segments[0].text == "你好"
    assert loaded.segments[0].idx == segment.idx
    assert loaded.embeddings[segment.idx][0] == 1
    assert loaded.speakers[1].count == 1
    assert loaded.speakers[1].centroid is not None
    assert loaded.threshold == 0.62
    assert loaded.audio_path.endswith(".wav")

    store.mark_ended(loaded, reason="exported")
    assert store.find_incomplete() == []


def test_ignored_session_is_not_offered_again(tmp_path):
    store = SessionStore(tmp_path)
    session = MeetingSession()
    session.start(now=datetime(2026, 5, 2, 8, 0, 0), mono=0)
    store.begin(session)
    assert store.find_incomplete()
    store.mark_ended(reason="ignored")
    assert store.find_incomplete() == []
