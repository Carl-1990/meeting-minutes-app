"""Append-only JSONL autosave so a crashed meeting can be restored."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from app.core.session import MeetingSession, default_speakers
from app.utils.paths import sessions_dir


def _stamp(when: datetime | None = None) -> str:
    return (when or datetime.now()).strftime("%Y%m%d_%H%M%S")


class SessionStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else sessions_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path: Path | None = None

    def begin(self, session: MeetingSession, wav_path: Path | None = None) -> Path:
        when = session.started_at or datetime.now()
        self.path = self.directory / f"{_stamp(when)}.jsonl"
        # Avoid clobbering an in-progress file with the same second.
        suffix = 2
        while self.path.exists():
            self.path = self.directory / f"{_stamp(when)}_{suffix}.jsonl"
            suffix += 1
        if wav_path is None:
            wav_path = self.path.with_suffix(".wav")
        session.audio_path = str(wav_path)
        self._write(
            {
                "type": "meta",
                "started_at": when.isoformat(timespec="seconds"),
                "mode": session.mode,
                "threshold": session.threshold,
                "audio_path": session.audio_path,
            }
        )
        return self.path

    def adopt(self, path: Path) -> None:
        self.path = Path(path)

    def append_segment(self, session: MeetingSession, segment) -> None:
        speaker = session.speakers[segment.speaker_id]
        embedding = session.embeddings.get(segment.idx)
        self._write(
            {
                "type": "segment",
                "idx": segment.idx,
                "start": segment.start,
                "end": segment.end,
                "speaker_id": segment.speaker_id,
                "text": segment.text,
                "source": segment.source,
                "speaker_name": speaker.name,
                "count": speaker.count,
                "centroid": None
                if speaker.centroid is None
                else np.asarray(speaker.centroid, dtype=float).tolist(),
                "embedding": None if embedding is None else np.asarray(embedding, dtype=float).tolist(),
            }
        )

    def append_rename(self, speaker_id: int, name: str) -> None:
        self._write({"type": "rename", "speaker_id": speaker_id, "name": name})

    def append_mode(self, mode: str, threshold: float) -> None:
        self._write({"type": "mode", "mode": mode, "threshold": threshold})

    def append_pause(self, paused_total: float) -> None:
        self._write({"type": "pause", "paused_total": paused_total})

    def append_resume(self, paused_total: float) -> None:
        self._write({"type": "resume", "paused_total": paused_total})

    def mark_ended(self, session: MeetingSession | None = None, reason: str = "exported") -> None:
        payload = {
            "type": "end",
            "reason": reason,
            "ended_at": datetime.now().isoformat(timespec="seconds"),
        }
        if session is not None and session.ended_at is not None:
            payload["ended_at"] = session.ended_at.isoformat(timespec="seconds")
            payload["duration_seconds"] = session.duration_seconds
        self._write(payload)

    def _write(self, payload: dict) -> None:
        if self.path is None:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def find_incomplete(self) -> list[Path]:
        return find_incomplete(self.directory)


def find_incomplete(directory: Path | None = None) -> list[Path]:
    directory = Path(directory) if directory is not None else sessions_dir()
    if not directory.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(directory.glob("*.jsonl")):
        ended = False
        has_meta = False
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "meta":
                has_meta = True
            if event.get("type") == "end":
                ended = True
        if has_meta and not ended:
            found.append(path)
    return found


def load_session(path: Path) -> MeetingSession:
    session = MeetingSession()
    session.speakers = default_speakers()
    text = Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "meta":
            started = event.get("started_at")
            if started:
                session.started_at = datetime.fromisoformat(started)
            session.mode = event.get("mode") or "auto"
            if session.mode not in ("auto", "manual"):
                session.mode = "auto"
            try:
                session.threshold = float(event.get("threshold", 0.55))
            except (TypeError, ValueError):
                session.threshold = 0.55
            session.audio_path = event.get("audio_path")
        elif kind == "rename":
            speaker_id = int(event["speaker_id"])
            if speaker_id in session.speakers:
                session.speakers[speaker_id].name = str(event.get("name") or "")
        elif kind == "mode":
            mode = event.get("mode")
            if mode in ("auto", "manual"):
                session.mode = mode
            if "threshold" in event:
                session.threshold = float(event["threshold"])
        elif kind == "segment":
            speaker_id = int(event["speaker_id"])
            name = event.get("speaker_name")
            if name and speaker_id in session.speakers:
                session.speakers[speaker_id].name = str(name)
            centroid = event.get("centroid")
            if centroid is not None and speaker_id in session.speakers:
                session.speakers[speaker_id].centroid = np.asarray(centroid, dtype=np.float64)
                session.speakers[speaker_id].count = int(event.get("count") or 1)
            source = event.get("source") or "auto"
            if source not in ("auto", "manual", "corrected"):
                source = "auto"
            segment = session.add_segment(
                float(event.get("start") or 0),
                float(event.get("end") or 0),
                speaker_id,
                str(event.get("text") or ""),
                source,
                embedding=event.get("embedding"),
            )
            # Preserve the original index when it was stored.
            stored_idx = event.get("idx")
            if stored_idx is not None:
                old = segment.idx
                segment.idx = int(stored_idx)
                if old in session.embeddings and segment.idx != old:
                    session.embeddings[segment.idx] = session.embeddings.pop(old)
                session._next_idx = max(session._next_idx, segment.idx + 1)
        elif kind == "end":
            ended = event.get("ended_at")
            if ended:
                session.ended_at = datetime.fromisoformat(ended)
            if event.get("duration_seconds") is not None:
                session.duration_seconds = float(event["duration_seconds"])
    if session.segments and session.duration_seconds <= 0:
        session.duration_seconds = session.segments[-1].end
    session._mono_origin = None
    session._pause_started_mono = None
    return session
