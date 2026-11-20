"""Export a meeting transcript to a .docx on the Desktop, plus the recording."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from app.core.session import SPEAKER_COLORS, MeetingSession, format_timestamp
from app.utils.paths import desktop_dir

_SUFFIXES = (".docx", ".m4a", ".wav")


@dataclass
class ExportResult:
    docx_path: Path
    audio_path: Path | None


def allocate_stem(directory: Path, stem: str) -> str:
    candidate = stem
    number = 2
    while any((directory / f"{candidate}{ext}").exists() for ext in _SUFFIXES):
        candidate = f"{stem}_{number}"
        number += 1
        if number > 1000:
            raise RuntimeError("无法生成不重名的文件名")
    return candidate


def _hex_to_rgb(color: str) -> RGBColor:
    value = color.lstrip("#")
    return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _set_run_font(run, name: str = "PingFang SC", size: int | None = None) -> None:
    run.font.name = name
    if size is not None:
        run.font.size = Pt(size)
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(fonts)
    fonts.set(qn("w:ascii"), name)
    fonts.set(qn("w:hAnsi"), name)
    fonts.set(qn("w:eastAsia"), name)


def _info_paragraph(doc: Document, label: str, value: str) -> None:
    paragraph = doc.add_paragraph()
    label_run = paragraph.add_run(f"{label}：")
    label_run.bold = True
    _set_run_font(label_run, size=12)
    value_run = paragraph.add_run(value)
    _set_run_font(value_run, size=12)


def build_document(session: MeetingSession) -> Document:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "PingFang SC"
    style.font.size = Pt(12)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "PingFang SC")

    title = doc.add_heading("会议逐字稿", level=0)
    for run in title.runs:
        _set_run_font(run, size=22)

    started = session.started_at
    ended = session.ended_at
    date_text = started.strftime("%Y-%m-%d") if started else "—"
    start_text = started.strftime("%H:%M:%S") if started else "—"
    end_text = ended.strftime("%H:%M:%S") if ended else "—"
    names = session.participant_names()
    _info_paragraph(doc, "日期", date_text)
    _info_paragraph(doc, "开始时间", start_text)
    _info_paragraph(doc, "结束时间", end_text)
    _info_paragraph(doc, "时长", format_timestamp(session.duration_seconds))
    _info_paragraph(doc, "参会发言人", "、".join(names) if names else "无")

    if not session.segments:
        empty = doc.add_paragraph("（本次会议没有识别到发言）")
        for run in empty.runs:
            _set_run_font(run, size=12)
        return doc

    for segment in session.segments:
        paragraph = doc.add_paragraph()
        ts = paragraph.add_run(f"[{format_timestamp(segment.start)}] ")
        ts.font.color.rgb = RGBColor(0x75, 0x75, 0x75)
        _set_run_font(ts, size=12)
        name = session.speakers[segment.speaker_id].name
        name_run = paragraph.add_run(f"{name}：")
        name_run.bold = True
        color = SPEAKER_COLORS[(segment.speaker_id - 1) % len(SPEAKER_COLORS)]
        name_run.font.color.rgb = _hex_to_rgb(color)
        _set_run_font(name_run, size=12)
        body = paragraph.add_run(segment.text)
        _set_run_font(body, size=12)
    return doc


def _convert_audio(source: Path, destination_stem: Path) -> Path | None:
    if not source.is_file() or source.stat().st_size <= 44:
        return None
    m4a = destination_stem.with_suffix(".m4a")
    wav = destination_stem.with_suffix(".wav")
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["afconvert", "-f", "m4af", "-d", "aac", str(source), str(m4a)],
                check=True,
                capture_output=True,
                timeout=600,
            )
            if m4a.is_file() and m4a.stat().st_size > 0:
                return m4a
        except Exception:
            pass
    shutil.copyfile(source, wav)
    return wav


def export_meeting(
    session: MeetingSession,
    output_dir: Path | None = None,
    audio_source: Path | None = None,
) -> ExportResult:
    directory = Path(output_dir) if output_dir is not None else desktop_dir()
    directory.mkdir(parents=True, exist_ok=True)
    when = session.ended_at or session.started_at or datetime.now()
    stem = allocate_stem(directory, f"会议纪要_{when.strftime('%Y%m%d_%H%M')}")
    docx_path = directory / f"{stem}.docx"
    document = build_document(session)
    document.save(str(docx_path))
    audio_path = None
    source = Path(audio_source) if audio_source is not None else (
        Path(session.audio_path) if session.audio_path else None
    )
    if source is not None:
        audio_path = _convert_audio(source, directory / stem)
    return ExportResult(docx_path=docx_path, audio_path=audio_path)
