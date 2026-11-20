"""Left control column: meeting buttons and the ten speaker buttons."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.core.session import SPEAKER_COLORS, MeetingSession


class SpeakerButton(QPushButton):
    selected = Signal(int)
    rename_requested = Signal(int)

    def __init__(self, speaker_id: int, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.speaker_id = speaker_id
        self.full_name = text
        self.setObjectName(f"speakerButton{speaker_id}")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.selected.emit(self.speaker_id))
        self.setToolTip("单击指定发言人，双击改名")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_full_name(self, name: str) -> None:
        self.full_name = name
        metrics = self.fontMetrics()
        self.setText(metrics.elidedText(name, Qt.TextElideMode.ElideRight, 160))
        self.setToolTip(f"{name}\n单击指定发言人，双击改名")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._timer.start(220)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._timer.stop()
            self.rename_requested.emit(self.speaker_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class RenameDialog(QDialog):
    def __init__(self, current: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("修改发言人姓名")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("输入姓名后回车确认。已有逐字稿会同步改名。"))
        self.edit = QLineEdit(current)
        self.edit.selectAll()
        layout.addWidget(self.edit)
        self.favorite = QCheckBox("保存为常用参会人")
        layout.addWidget(self.favorite)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.edit.returnPressed.connect(self.accept)
        layout.addWidget(buttons)
        self.resize(320, 160)

    def name(self) -> str:
        return self.edit.text().strip()

    def save_favorite(self) -> bool:
        return self.favorite.isChecked()


class SettingsDialog(QDialog):
    def __init__(self, threshold: float, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.30, 0.90)
        self.threshold.setSingleStep(0.01)
        self.threshold.setDecimals(2)
        self.threshold.setValue(threshold)
        form.addRow("声纹相似度阈值", self.threshold)
        layout.addLayout(form)
        note = QLabel(
            "越高越不容易把不同的人认成同一个人，也越容易新开一个发言人。默认 0.55。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        limit = QLabel(
            "单个笔记本麦克风、多人同时说话或声音相近时，自动区分发言人的准确率会下降。"
            "建议在会议室使用全向麦克风。"
        )
        limit.setWordWrap(True)
        layout.addWidget(limit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(420, 220)


class Sidebar(QWidget):
    start_clicked = Signal()
    pause_clicked = Signal()
    end_clicked = Signal()
    speaker_selected = Signal(int)
    rename_requested = Signal(int)
    favorite_chosen = Signal(int, str)
    mode_changed = Signal(str)
    settings_requested = Signal()

    def __init__(self, session: MeetingSession, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("sidebar")
        self.setFixedWidth(220)
        self._speaking_id: int | None = None
        self._selected_id: int | None = None
        self._favorites: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.auto_radio = QRadioButton("自动识别发言人")
        self.manual_radio = QRadioButton("手动选择发言人")
        self.auto_radio.setChecked(session.mode != "manual")
        self.manual_radio.setChecked(session.mode == "manual")
        group = QButtonGroup(self)
        group.addButton(self.auto_radio)
        group.addButton(self.manual_radio)
        self.auto_radio.toggled.connect(lambda checked: checked and self.mode_changed.emit("auto"))
        self.manual_radio.toggled.connect(lambda checked: checked and self.mode_changed.emit("manual"))
        layout.addWidget(self.auto_radio)
        layout.addWidget(self.manual_radio)

        self.start_button = QPushButton("开始会议")
        self.start_button.setObjectName("startButton")
        self.start_button.setProperty("phase", "idle")
        self.start_button.clicked.connect(self.start_clicked.emit)
        self.pause_button = QPushButton("暂停会议")
        self.pause_button.setObjectName("pauseButton")
        self.pause_button.clicked.connect(self.pause_clicked.emit)
        self.end_button = QPushButton("结束会议")
        self.end_button.setObjectName("endButton")
        self.end_button.clicked.connect(self.end_clicked.emit)
        layout.addWidget(self.start_button)
        layout.addWidget(self.pause_button)
        layout.addWidget(self.end_button)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        self.speaker_buttons: dict[int, SpeakerButton] = {}
        for speaker_id in range(1, 11):
            button = SpeakerButton(speaker_id, session.speakers[speaker_id].name)
            button.selected.connect(self.speaker_selected.emit)
            button.rename_requested.connect(self.rename_requested.emit)
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda pos, sid=speaker_id, btn=button: self._favorite_menu(sid, btn, pos)
            )
            self.speaker_buttons[speaker_id] = button
            layout.addWidget(button)

        layout.addStretch(1)
        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.settings_button)
        self._restyle_speakers()

    def set_favorites(self, names: list[str]) -> None:
        self._favorites = list(names)

    def set_start_phase(self, phase: str, text: str) -> None:
        self.start_button.setText(text)
        self.start_button.setProperty("phase", phase)
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)
        self.start_button.update()

    def set_speaker_name(self, speaker_id: int, name: str) -> None:
        self.speaker_buttons[speaker_id].set_full_name(name)
        self._restyle_speakers()

    def reload_names(self) -> None:
        for speaker_id, button in self.speaker_buttons.items():
            button.set_full_name(self.session.speakers[speaker_id].name)
        self._restyle_speakers()

    def set_selected(self, speaker_id: int | None) -> None:
        self._selected_id = speaker_id
        self._restyle_speakers()

    def flash_speaker(self, speaker_id: int) -> None:
        self._speaking_id = speaker_id
        self._restyle_speakers()
        QTimer.singleShot(1500, self._clear_flash)

    def _clear_flash(self) -> None:
        self._speaking_id = None
        self._restyle_speakers()

    def _is_dark(self) -> bool:
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()
        return scheme == Qt.ColorScheme.Dark

    def _restyle_speakers(self) -> None:
        dark = self._is_dark()
        idle_bg = "#1E1E1E" if dark else "#FFFFFF"
        for speaker_id, button in self.speaker_buttons.items():
            color = SPEAKER_COLORS[speaker_id - 1]
            speaking = self._speaking_id == speaker_id
            selected = self._selected_id == speaker_id
            border = 3 if speaking or selected else 1
            if speaking:
                bg, fg = color, "#FFFFFF"
            else:
                bg, fg = idle_bg, color
            button.setStyleSheet(
                "QPushButton {"
                f"background-color: {bg}; color: {fg};"
                f"border: {border}px solid {color}; border-radius: 6px;"
                "text-align: left; padding: 6px 10px; font-weight: 600;"
                "}"
            )

    def _favorite_menu(self, speaker_id: int, button: QPushButton, pos) -> None:
        if not self._favorites:
            return
        menu = QMenu(self)
        menu.addAction("常用参会人").setEnabled(False)
        for name in self._favorites:
            menu.addAction(
                name,
                lambda checked=False, n=name, sid=speaker_id: self.favorite_chosen.emit(sid, n),
            )
        menu.exec(button.mapToGlobal(pos))
