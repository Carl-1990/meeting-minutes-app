"""Main window: sidebar, live transcript, and the meeting lifecycle."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMessageBox, QWidget

from app.core.asr_engine import create_asr_engine
from app.core.diarizer import OnlineDiarizer, create_speaker_embedder
from app.core.pipeline import MeetingPipeline
from app.core.punctuator import create_punctuator
from app.core.session import MeetingSession, MeetingState
from app.core.vad import create_speech_segmenter
from app.export.docx_exporter import export_meeting
from app.ui.sidebar import RenameDialog, SettingsDialog, Sidebar
from app.ui.transcript_view import TranscriptView
from app.utils.autosave import SessionStore, find_incomplete, load_session
from app.utils.mic_permission import (
    authorization_status,
    open_microphone_settings,
    request_microphone_access,
)
from app.utils.paths import load_user_settings, models_dir, resource_path, save_user_settings

log = logging.getLogger(__name__)

MIC_MISSING = "未检测到麦克风，请检查连接或系统隐私设置"


class _GrantBridge(QObject):
    granted = Signal(bool)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("会议纪要")
        qss = resource_path("app", "ui", "styles.qss")
        if qss.is_file():
            self.setStyleSheet(qss.read_text(encoding="utf-8"))
        self.resize(1040, 700)
        self.setMinimumSize(860, 560)
        icon = resource_path("build", "icon.png")
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))

        settings = load_user_settings()
        self.session = MeetingSession()
        self.session.threshold = float(settings["threshold"])
        self.diarizer = OnlineDiarizer(threshold=self.session.threshold)
        self.store = SessionStore()
        self.pipeline: MeetingPipeline | None = None
        self._engines_ready = False
        self._asr = None
        self._segmenter = None
        self._embedder = None
        self._punctuator = None
        self._engine_warnings: list[str] = []
        self._mic_ready = False
        self._pending_recovery = False
        self._recovery_path: Path | None = None
        self._user_requested_end = False
        self._export_started = False
        self._favorites = list(settings["favorites"])
        self._bridge = _GrantBridge()
        self._bridge.granted.connect(self._start_after_permission)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.sidebar = Sidebar(self.session)
        self.sidebar.set_favorites(self._favorites)
        self.transcript = TranscriptView(self.session)
        layout.addWidget(self.sidebar)
        layout.addWidget(self.transcript, 1)
        self.setCentralWidget(central)

        self.sidebar.start_clicked.connect(self.start_meeting)
        self.sidebar.pause_clicked.connect(self.toggle_pause)
        self.sidebar.end_clicked.connect(self.end_meeting)
        self.sidebar.speaker_selected.connect(self.select_speaker)
        self.sidebar.rename_requested.connect(self.prompt_rename)
        self.sidebar.favorite_chosen.connect(self.apply_favorite)
        self.sidebar.mode_changed.connect(self.set_mode)
        self.sidebar.settings_requested.connect(self.open_settings)
        self.transcript.reassign_requested.connect(self.reassign_segment)

        self._clock = QTimer(self)
        self._clock.setInterval(200)
        self._clock.timeout.connect(self._tick)
        self._clock.start()
        self.refresh_buttons()
        QTimer.singleShot(0, self._maybe_recover)

    def refresh_buttons(self) -> None:
        state = self.session.state
        self.sidebar.pause_button.setEnabled(self.session.can_pause() or self.session.can_resume())
        self.sidebar.end_button.setEnabled(self.session.can_end() or self._pending_recovery)
        if state == MeetingState.RECORDING:
            self.sidebar.pause_button.setText("暂停会议")
            if self._mic_ready:
                self.sidebar.set_start_phase("recording", "会议进行中")
                self.transcript.set_status("会议进行中")
            else:
                self.sidebar.set_start_phase("waiting", "开始会议")
                self.transcript.set_status("正在检测麦克风")
        elif state == MeetingState.PAUSED:
            self.sidebar.pause_button.setText("继续会议")
            text = "会议进行中" if self._mic_ready else "开始会议"
            self.sidebar.set_start_phase("paused", text)
            self.transcript.set_status("已暂停")
        else:
            self.sidebar.pause_button.setText("暂停会议")
            self.sidebar.set_start_phase("idle", "开始会议")
            if self._pending_recovery:
                self.transcript.set_status("已恢复未完成的会议")
            else:
                self.transcript.set_status("就绪")

    def set_mode(self, mode: str) -> None:
        if mode not in ("auto", "manual"):
            return
        self.session.mode = mode  # type: ignore[assignment]
        if mode == "manual" and self.session.manual_speaker_id is None:
            self.session.manual_speaker_id = self.sidebar._selected_id or 1
            self.sidebar.set_selected(self.session.manual_speaker_id)
        if mode == "auto":
            self.session.forced_speaker_id = None
            self.sidebar.set_selected(None)
        self.store.append_mode(mode, self.diarizer.threshold)
        self.statusBar().showMessage("自动识别发言人" if mode == "auto" else "手动选择发言人")

    def select_speaker(self, speaker_id: int) -> None:
        name = self.session.speakers[speaker_id].name
        if self.session.mode == "manual":
            self.session.manual_speaker_id = speaker_id
            self.sidebar.set_selected(speaker_id)
            self.statusBar().showMessage(f"当前发言人：{name}")
        else:
            self.session.forced_speaker_id = speaker_id
            self.sidebar.set_selected(speaker_id)
            self.statusBar().showMessage(f"下一句将归为{name}")

    def prompt_rename(self, speaker_id: int) -> None:
        current = self.session.speakers[speaker_id].name
        dialog = RenameDialog(current, self)
        if dialog.exec() != RenameDialog.DialogCode.Accepted:
            return
        self._apply_name(speaker_id, dialog.name(), dialog.save_favorite())

    def apply_favorite(self, speaker_id: int, name: str) -> None:
        self._apply_name(speaker_id, name, save_favorite=False)

    def rename_speaker(self, speaker_id: int, name: str) -> None:
        self._apply_name(speaker_id, name, save_favorite=False)

    def _apply_name(self, speaker_id: int, name: str, save_favorite: bool) -> None:
        try:
            cleaned = self.session.rename_speaker(speaker_id, name)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        self.sidebar.set_speaker_name(speaker_id, cleaned)
        self.transcript.refresh_names()
        self.store.append_rename(speaker_id, cleaned)
        if save_favorite:
            self._remember_favorite(cleaned)
        self.statusBar().showMessage(f"已将发言人{speaker_id}改名为{cleaned}")

    def _remember_favorite(self, name: str) -> None:
        if name not in self._favorites:
            self._favorites.insert(0, name)
            self._favorites = self._favorites[:20]
        settings = load_user_settings()
        settings["favorites"] = self._favorites
        save_user_settings(settings)
        self.sidebar.set_favorites(self._favorites)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.diarizer.threshold, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        self.diarizer.threshold = float(dialog.threshold.value())
        self.session.threshold = self.diarizer.threshold
        settings = load_user_settings()
        settings["threshold"] = self.diarizer.threshold
        settings["favorites"] = self._favorites
        save_user_settings(settings)
        self.store.append_mode(self.session.mode, self.diarizer.threshold)
        self.statusBar().showMessage(f"声纹阈值已设为 {self.diarizer.threshold:.2f}")

    def start_meeting(self) -> None:
        if self.session.state != MeetingState.IDLE:
            return
        if self.pipeline is not None and self.pipeline.isRunning():
            return
        status = authorization_status()
        if status in ("denied", "restricted"):
            self._offer_mic_settings(MIC_MISSING)
            return
        if status == "not_determined":
            self.statusBar().showMessage("正在请求麦克风权限…")
            request_microphone_access(lambda granted: self._bridge.granted.emit(bool(granted)))
            return
        self._start_pipeline()

    def _start_after_permission(self, granted: bool) -> None:
        if not granted:
            self._offer_mic_settings(MIC_MISSING)
            return
        self._start_pipeline()

    def _ensure_engines(self) -> None:
        if self._engines_ready:
            return
        self._asr = create_asr_engine()
        self._segmenter = create_speech_segmenter()
        root = models_dir()
        speaker_model = root / "speaker" / "eres2net.onnx"
        self._embedder, embed_error = create_speaker_embedder(speaker_model)
        self._punctuator = create_punctuator()
        warnings = []
        if not getattr(self._asr, "available", False):
            warnings.append(f"语音识别模型未加载：{getattr(self._asr, 'reason', '')}")
        if embed_error:
            warnings.append(f"说话人模型未加载：{embed_error}")
        if not getattr(self._punctuator, "available", False) and getattr(self._punctuator, "reason", ""):
            warnings.append(f"标点模型未加载：{self._punctuator.reason}")
        self._engine_warnings = [item for item in warnings if item]
        self._engines_ready = True

    def _start_pipeline(self) -> None:
        self._ensure_engines()
        keep = self._pending_recovery
        self.session.start(keep_existing=keep)
        self.session.threshold = self.diarizer.threshold
        if keep and self._recovery_path is not None:
            self.store.adopt(self._recovery_path)
        else:
            self.transcript.reload()
            self.sidebar.reload_names()
        self._pending_recovery = False
        self._user_requested_end = False
        self._export_started = False
        self._mic_ready = False
        if keep and self.session.audio_path and self.store.path is not None:
            wav_path = Path(self.session.audio_path)
        else:
            self.store.begin(self.session)
            wav_path = Path(self.session.audio_path)
        offset = self.session.active_duration()
        self.pipeline = MeetingPipeline(
            self._asr,
            self._segmenter,
            self._embedder,
            self._punctuator,
            wav_path,
            start_offset_seconds=offset,
        )
        self.pipeline.audio_level.connect(self.transcript.set_level)
        self.pipeline.mic_ready.connect(self._on_mic_ready)
        self.pipeline.partial_text.connect(self._on_partial)
        self.pipeline.finalized.connect(self._on_finalized)
        self.pipeline.error.connect(self._on_pipeline_error)
        self.pipeline.finished.connect(self._on_pipeline_finished)
        self.pipeline.start()
        self.refresh_buttons()
        self.statusBar().showMessage("正在检测麦克风…")
        if self._engine_warnings:
            QMessageBox.warning(
                self,
                "语音模型",
                "未找到完整的离线语音模型，本次可以录音，但可能无法转写或区分发言人。\n\n"
                + "\n".join(self._engine_warnings)
                + "\n\n开发环境请先运行 build/download_models.sh，或安装包含模型的完整安装包。",
            )

    def toggle_pause(self) -> None:
        if self.session.state == MeetingState.RECORDING:
            self.session.pause()
            if self.pipeline is not None:
                self.pipeline.set_accepting(False)
            self.store.append_pause(self.session.paused_total)
            self.refresh_buttons()
            self.statusBar().showMessage("已暂停。暂停期间的声音不会写入录音，也不会转写。")
        elif self.session.state == MeetingState.PAUSED:
            self.session.resume()
            if self.pipeline is not None:
                self.pipeline.set_accepting(True)
            self.store.append_resume(self.session.paused_total)
            self.refresh_buttons()
            self.statusBar().showMessage("已继续会议")

    def end_meeting(self) -> None:
        if self.session.state == MeetingState.IDLE and self._pending_recovery:
            if not self._confirm_end():
                return
            self._export_current()
            return
        if not self.session.can_end():
            return
        if not self._confirm_end():
            return
        self._user_requested_end = True
        self.sidebar.end_button.setEnabled(False)
        self.sidebar.pause_button.setEnabled(False)
        self.statusBar().showMessage("正在结束会议并导出…")
        if self.pipeline is not None and self.pipeline.isRunning():
            self.pipeline.request_stop()
        else:
            self._export_current()

    def _confirm_end(self) -> bool:
        answer = QMessageBox.question(
            self,
            "结束会议",
            "确定结束会议并导出 Word 到桌面吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_mic_ready(self, ok: bool) -> None:
        if ok:
            self._mic_ready = True
            if self.session.state == MeetingState.RECORDING:
                self.refresh_buttons()
                self.statusBar().showMessage("正在录音并转写")
            return
        self._mic_ready = False
        self.refresh_buttons()
        self.statusBar().showMessage(MIC_MISSING)
        self._offer_mic_settings(MIC_MISSING)

    def _on_partial(self, text: str, seconds: float) -> None:
        self.transcript.set_partial(text, seconds, self._partial_speaker_name())

    def _partial_speaker_name(self) -> str | None:
        if self.session.mode == "manual":
            speaker_id = self.session.manual_speaker_id or 1
            return self.session.speakers[speaker_id].name
        if self.session.forced_speaker_id:
            return self.session.speakers[self.session.forced_speaker_id].name
        if self.session.segments:
            return self.session.speakers[self.session.segments[-1].speaker_id].name
        return None

    def _on_finalized(self, text: str, start: float, end: float, speech_seconds: float, embedding) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            self.transcript.set_partial("", end, None)
            return
        previous = self.session.segments[-1].speaker_id if self.session.segments else None
        speaker_id, source, forced = self.diarizer.assign_utterance(
            self.session.speakers,
            embedding,
            speech_seconds,
            previous,
            self.session.mode,
            self.session.manual_speaker_id,
            self.session.forced_speaker_id,
        )
        self.session.forced_speaker_id = forced
        if forced is None and self.session.mode == "auto":
            self.sidebar.set_selected(None)
        segment = self.session.add_segment(start, end, speaker_id, cleaned, source, embedding=embedding)
        self.transcript.append_segment()
        self.store.append_segment(self.session, segment)
        self.sidebar.flash_speaker(speaker_id)
        self.transcript.set_partial("", end, None)

    def reassign_segment(self, idx: int, speaker_id: int) -> None:
        try:
            segment = self.session.reassign_segment(idx, speaker_id)
        except (KeyError, ValueError) as exc:
            self.statusBar().showMessage(str(exc))
            return
        embedding = self.session.embeddings.get(idx)
        if embedding is not None:
            self.diarizer.absorb(self.session.speakers[speaker_id], embedding)
        self.transcript.refresh_names()
        self.store.append_segment(self.session, segment)
        self.sidebar.flash_speaker(speaker_id)
        name = self.session.speakers[speaker_id].name
        self.statusBar().showMessage(f"已将该句改为{name}，声纹已并入该发言人")

    def _on_pipeline_error(self, message: str) -> None:
        log.error("pipeline error: %s", message)
        shown = message or MIC_MISSING
        if "麦克风" not in shown:
            shown = MIC_MISSING if "sounddevice" in shown.lower() or "input" in shown.lower() else shown
        self.statusBar().showMessage(shown)
        if self._user_requested_end:
            return
        early = (
            self.session.state in (MeetingState.RECORDING, MeetingState.PAUSED)
            and not self.session.segments
            and (self.pipeline is None or self.pipeline.active_seconds < 1.5)
        )
        if early:
            self.session.fail_start()
            self._mic_ready = False
            self.refresh_buttons()
            self._offer_mic_settings(shown if "麦克风" in shown else MIC_MISSING)
        elif self.session.state == MeetingState.RECORDING:
            self.session.pause()
            if self.pipeline is not None:
                self.pipeline.set_accepting(False)
            self.refresh_buttons()
            QMessageBox.warning(self, "会议纪要", shown)

    def _on_pipeline_finished(self) -> None:
        if self._user_requested_end:
            self._export_current()

    def _export_current(self) -> None:
        if self._export_started:
            return
        self._export_started = True
        duration = self.pipeline.active_seconds if self.pipeline is not None else self.session.duration_seconds
        if self.session.state in (MeetingState.RECORDING, MeetingState.PAUSED):
            self.session.begin_export(duration=duration)
        elif self.session.ended_at is None:
            self.session.ended_at = datetime.now()
            if not self.session.duration_seconds:
                self.session.duration_seconds = duration
        try:
            result = export_meeting(self.session)
        except Exception as exc:
            log.exception("export failed")
            QMessageBox.critical(self, "导出失败", f"无法写入桌面上的 Word 文件：\n{exc}")
            if self.session.state == MeetingState.EXPORTING:
                self.session.state = MeetingState.IDLE
                self._pending_recovery = True
            self._export_started = False
            self._user_requested_end = False
            self.refresh_buttons()
            return
        self.store.mark_ended(self.session, reason="exported")
        if self.session.state == MeetingState.EXPORTING:
            self.session.finish_export()
        self._pending_recovery = False
        self._recovery_path = None
        self._user_requested_end = False
        self._mic_ready = False
        self.store.path = None
        self.refresh_buttons()
        self._show_saved(result)

    def _show_saved(self, result) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("导出完成")
        box.setText(f"已保存到桌面：{result.docx_path.name}")
        if result.audio_path is not None:
            box.setInformativeText(f"录音已保存为：{result.audio_path.name}")
        open_btn = box.addButton("打开文件", QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton("打开所在文件夹", QMessageBox.ButtonRole.ActionRole)
        box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked == open_btn:
            _open_path(result.docx_path)
        elif clicked == folder_btn:
            _reveal(result.docx_path)

    def _offer_mic_settings(self, message: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("麦克风")
        box.setText(message)
        open_btn = box.addButton("打开系统设置", QMessageBox.ButtonRole.ActionRole)
        box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == open_btn:
            open_microphone_settings()

    def _tick(self) -> None:
        if self.pipeline is not None and self.pipeline.isRunning() and self.session.state != MeetingState.IDLE:
            self.transcript.set_clock(self.pipeline.active_seconds)
        elif self.session.segments:
            self.transcript.set_clock(self.session.segments[-1].end)
        else:
            self.transcript.set_clock(0)

    def _maybe_recover(self) -> None:
        incomplete = find_incomplete(self.store.directory)
        if not incomplete:
            return
        path = incomplete[-1]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("恢复会议")
        box.setText("发现未完成的会议，是否恢复/导出？")
        restore_btn = box.addButton("恢复", QMessageBox.ButtonRole.AcceptRole)
        export_btn = box.addButton("导出", QMessageBox.ButtonRole.ActionRole)
        ignore_btn = box.addButton("忽略", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked == ignore_btn or clicked is None:
            self.store.adopt(path)
            self.store.mark_ended(reason="ignored")
            self.store.path = None
            return
        try:
            loaded = load_session(path)
        except Exception as exc:
            log.exception("recovery failed")
            QMessageBox.warning(self, "恢复失败", str(exc))
            return
        if clicked == export_btn:
            self.session.restore_from(loaded)
            self.store.adopt(path)
            self.transcript.reload()
            self.sidebar.reload_names()
            self._export_current()
            return
        self.session.restore_from(loaded)
        self.diarizer.threshold = self.session.threshold
        self.store.adopt(path)
        self._recovery_path = path
        self._pending_recovery = True
        if self.session.mode == "manual":
            self.sidebar.manual_radio.setChecked(True)
        else:
            self.sidebar.auto_radio.setChecked(True)
        self.transcript.reload()
        self.sidebar.reload_names()
        self.refresh_buttons()
        self.statusBar().showMessage("已恢复未完成的会议。点击「开始会议」继续录音，或点击「结束会议」导出。")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.session.state in (MeetingState.RECORDING, MeetingState.PAUSED):
            answer = QMessageBox.question(
                self,
                "退出",
                "会议还在进行。退出前要结束并导出吗？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Yes:
                self._user_requested_end = True
                if self.pipeline is not None and self.pipeline.isRunning():
                    self.pipeline.request_stop()
                    self.pipeline.wait(8000)
                self._export_current()
        if self.pipeline is not None and self.pipeline.isRunning():
            self.pipeline.request_stop()
            self.pipeline.wait(3000)
        event.accept()


def _open_path(path: Path) -> None:
    import subprocess
    import sys

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif sys.platform == "win32":
            import os

            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        log.exception("open file failed")


def _reveal(path: Path) -> None:
    import subprocess
    import sys

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            _open_path(path.parent)
    except Exception:
        log.exception("reveal failed")
