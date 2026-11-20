"""Meeting state machine and transcript data model.

Display and export always read the speaker name from ``speakers[id].name``,
so renaming a speaker updates every past line and the Word file.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal

import numpy as np

MAX_SPEAKERS = 10

SPEAKER_COLORS = [
    "#1565C0",
    "#C62828",
    "#2E7D32",
    "#6A1B9A",
    "#EF6C00",
    "#00838F",
    "#AD1457",
    "#4527A0",
    "#558B2F",
    "#4E342E",
]

SegmentSource = Literal["auto", "manual", "corrected"]


class MeetingState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    EXPORTING = "exporting"


def format_timestamp(seconds: float) -> str:
    """Format active meeting time as ``[HH:MM:SS]`` without the brackets."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


@dataclass
class Speaker:
    id: int
    name: str
    centroid: np.ndarray | None = None
    count: int = 0


@dataclass
class Segment:
    idx: int
    start: float
    end: float
    speaker_id: int
    text: str
    source: SegmentSource


def default_speakers() -> dict[int, Speaker]:
    return {i: Speaker(id=i, name=f"发言人{i}") for i in range(1, MAX_SPEAKERS + 1)}


@dataclass
class MeetingSession:
    started_at: datetime | None = None
    ended_at: datetime | None = None
    speakers: dict[int, Speaker] = field(default_factory=default_speakers)
    segments: list[Segment] = field(default_factory=list)
    paused_total: float = 0.0
    state: MeetingState = MeetingState.IDLE
    mode: Literal["auto", "manual"] = "auto"
    threshold: float = 0.55
    duration_seconds: float = 0.0
    manual_speaker_id: int | None = None
    forced_speaker_id: int | None = None
    audio_path: str | None = None
    embeddings: dict[int, np.ndarray] = field(default_factory=dict)
    _next_idx: int = 1
    _mono_origin: float | None = None
    _pause_started_mono: float | None = None
    _elapsed_offset: float = 0.0

    def can_start(self) -> bool:
        return self.state == MeetingState.IDLE

    def can_pause(self) -> bool:
        return self.state == MeetingState.RECORDING

    def can_resume(self) -> bool:
        return self.state == MeetingState.PAUSED

    def can_end(self) -> bool:
        return self.state in (MeetingState.RECORDING, MeetingState.PAUSED)

    def _check_speaker(self, speaker_id: int) -> None:
        if speaker_id not in self.speakers:
            raise ValueError(f"发言人编号无效：{speaker_id}")

    def _clear_meeting(self) -> None:
        mode = self.mode
        threshold = self.threshold
        self.speakers = default_speakers()
        self.segments = []
        self.embeddings = {}
        self.started_at = None
        self.ended_at = None
        self.paused_total = 0.0
        self.duration_seconds = 0.0
        self.manual_speaker_id = None
        self.forced_speaker_id = None
        self.audio_path = None
        self._next_idx = 1
        self._mono_origin = None
        self._pause_started_mono = None
        self._elapsed_offset = 0.0
        self.mode = mode
        self.threshold = threshold

    def start(
        self,
        now: datetime | None = None,
        mono: float | None = None,
        keep_existing: bool = False,
    ) -> None:
        if not self.can_start():
            raise ValueError("当前状态无法开始会议")
        if not keep_existing:
            self._clear_meeting()
        mono = time.monotonic() if mono is None else mono
        now = now or datetime.now()
        if self.started_at is None:
            self.started_at = now
        self.ended_at = None
        if keep_existing:
            self._elapsed_offset = self.segments[-1].end if self.segments else 0.0
        else:
            self._elapsed_offset = 0.0
        self.paused_total = 0.0
        self._pause_started_mono = None
        self._mono_origin = mono
        self.state = MeetingState.RECORDING

    def pause(self, mono: float | None = None) -> None:
        if not self.can_pause():
            raise ValueError("当前没有进行中的会议")
        self.state = MeetingState.PAUSED
        self._pause_started_mono = time.monotonic() if mono is None else mono

    def _close_pause(self, mono: float) -> None:
        if self._pause_started_mono is not None:
            self.paused_total += max(0.0, mono - self._pause_started_mono)
            self._pause_started_mono = None

    def resume(self, mono: float | None = None) -> None:
        if not self.can_resume():
            raise ValueError("会议没有暂停")
        mono = time.monotonic() if mono is None else mono
        self._close_pause(mono)
        self.state = MeetingState.RECORDING

    def active_duration(self, mono: float | None = None) -> float:
        if self._mono_origin is None:
            return self._elapsed_offset
        now = time.monotonic() if mono is None else mono
        paused = self.paused_total
        if self.state == MeetingState.PAUSED and self._pause_started_mono is not None:
            paused += max(0.0, now - self._pause_started_mono)
        return max(0.0, self._elapsed_offset + (now - self._mono_origin) - paused)

    def begin_export(
        self,
        now: datetime | None = None,
        mono: float | None = None,
        duration: float | None = None,
    ) -> None:
        if not self.can_end():
            raise ValueError("当前状态无法结束会议")
        mono = time.monotonic() if mono is None else mono
        if self.state == MeetingState.PAUSED:
            self._close_pause(mono)
        self.ended_at = now or datetime.now()
        if duration is None:
            self.duration_seconds = self.active_duration(mono)
        else:
            self.duration_seconds = float(duration)
        self.state = MeetingState.EXPORTING
        self._pause_started_mono = None

    def finish_export(self) -> None:
        if self.state != MeetingState.EXPORTING:
            raise ValueError("当前没有正在导出的会议")
        self.state = MeetingState.IDLE
        self._mono_origin = None

    def fail_start(self) -> None:
        if self.state not in (MeetingState.RECORDING, MeetingState.PAUSED):
            return
        self.state = MeetingState.IDLE
        self._pause_started_mono = None
        self._mono_origin = None

    def rename_speaker(self, speaker_id: int, name: str) -> str:
        self._check_speaker(speaker_id)
        cleaned = " ".join(name.strip().split())
        if not cleaned:
            raise ValueError("姓名不能为空")
        if len(cleaned) > 32:
            cleaned = cleaned[:32]
        self.speakers[speaker_id].name = cleaned
        return cleaned

    def add_segment(
        self,
        start: float,
        end: float,
        speaker_id: int,
        text: str,
        source: SegmentSource,
        embedding: np.ndarray | list[float] | None = None,
    ) -> Segment:
        self._check_speaker(speaker_id)
        if source not in ("auto", "manual", "corrected"):
            raise ValueError("无效的来源")
        segment = Segment(
            idx=self._next_idx,
            start=float(start),
            end=float(end),
            speaker_id=int(speaker_id),
            text=str(text),
            source=source,
        )
        self._next_idx += 1
        self.segments.append(segment)
        if embedding is not None:
            self.embeddings[segment.idx] = np.asarray(embedding, dtype=np.float64)
        return segment

    def segment_by_idx(self, idx: int) -> Segment | None:
        for segment in self.segments:
            if segment.idx == idx:
                return segment
        return None

    def reassign_segment(self, idx: int, speaker_id: int) -> Segment:
        self._check_speaker(speaker_id)
        segment = self.segment_by_idx(idx)
        if segment is None:
            raise KeyError(f"找不到记录 {idx}")
        segment.speaker_id = speaker_id
        segment.source = "corrected"
        return segment

    def format_line(self, segment: Segment) -> str:
        name = self.speakers[segment.speaker_id].name
        return f"[{format_timestamp(segment.start)}] {name}：{segment.text}"

    def participant_names(self) -> list[str]:
        names: list[str] = []
        for segment in self.segments:
            name = self.speakers[segment.speaker_id].name
            if name not in names:
                names.append(name)
        return names

    def restore_from(self, other: MeetingSession) -> None:
        """Copy a recovered meeting into this object, staying IDLE."""
        self.speakers = other.speakers
        self.segments = list(other.segments)
        self.embeddings = dict(other.embeddings)
        self.started_at = other.started_at
        self.ended_at = None
        self.paused_total = 0.0
        self.duration_seconds = other.duration_seconds
        self.mode = other.mode
        self.threshold = other.threshold
        self.manual_speaker_id = other.manual_speaker_id
        self.forced_speaker_id = None
        self.audio_path = other.audio_path
        self._next_idx = max((segment.idx for segment in self.segments), default=0) + 1
        self._elapsed_offset = self.segments[-1].end if self.segments else 0.0
        self._mono_origin = None
        self._pause_started_mono = None
        self.state = MeetingState.IDLE
