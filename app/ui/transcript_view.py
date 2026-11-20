"""Right-hand live transcript."""

from __future__ import annotations

import html

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QStyledItemDelegate,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from app.core.session import SPEAKER_COLORS, MeetingSession, format_timestamp
from app.ui.level_meter import LevelMeter


class TranscriptModel(QAbstractListModel):
    def __init__(self, session: MeetingSession, parent=None) -> None:
        super().__init__(parent)
        self.session = session

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self.session.segments)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.session.segments)):
            return None
        segment = self.session.segments[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self.session.format_line(segment)
        if role == Qt.ItemDataRole.UserRole:
            return segment.idx
        return None

    def reload(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    def append_row(self) -> None:
        row = len(self.session.segments) - 1
        if row < 0:
            return
        self.beginInsertRows(QModelIndex(), row, row)
        self.endInsertRows()

    def refresh_visible(self) -> None:
        if not self.session.segments:
            self.reload()
            return
        top = self.index(0, 0)
        bottom = self.index(len(self.session.segments) - 1, 0)
        self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.DisplayRole])


class TranscriptList(QListView):
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.scheduleDelayedItemsLayout()


class TranscriptDelegate(QStyledItemDelegate):
    def __init__(self, session: MeetingSession, parent=None) -> None:
        super().__init__(parent)
        self.session = session

    def _width(self, option) -> int:
        view = self.parent()
        if isinstance(view, QAbstractItemView):
            return max(120, view.viewport().width())
        if option.rect.width() > 20:
            return option.rect.width()
        return 640

    def _document(self, index, width: int) -> QTextDocument:
        segment = self.session.segments[index.row()]
        name = html.escape(self.session.speakers[segment.speaker_id].name)
        color = SPEAKER_COLORS[(segment.speaker_id - 1) % len(SPEAKER_COLORS)]
        timestamp = format_timestamp(segment.start)
        body = html.escape(segment.text).replace("\n", "<br>")
        doc = QTextDocument()
        doc.setHtml(
            f"<span style='color:#757575'>[{timestamp}]</span> "
            f"<span style='color:{color}; font-weight:600'>{name}：</span>"
            f"{body}"
        )
        doc.setTextWidth(max(40, width - 16))
        return doc

    def paint(self, painter, option, index) -> None:
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        elif index.row() % 2 == 0:
            painter.fillRect(option.rect, option.palette.alternateBase())
        doc = self._document(index, self._width(option))
        painter.translate(option.rect.left() + 8, option.rect.top() + 4)
        doc.drawContents(painter)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        doc = self._document(index, self._width(option))
        return QSize(int(self._width(option)), int(doc.size().height()) + 12)


class TranscriptView(QWidget):
    reassign_requested = Signal(int, int)

    def __init__(self, session: MeetingSession, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self._stick = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        self.status_label = QLabel("就绪")
        self.clock_label = QLabel("00:00:00")
        self.clock_label.setObjectName("clockLabel")
        self.mic_label = QLabel("🎙")
        self.meter = LevelMeter()
        header_layout.addWidget(self.status_label)
        header_layout.addSpacing(12)
        header_layout.addWidget(self.clock_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.mic_label)
        header_layout.addWidget(self.meter)
        root.addWidget(header)

        self.empty_label = QLabel("点击「开始会议」后，转写文字会显示在这里")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setContentsMargins(24, 24, 24, 24)
        root.addWidget(self.empty_label)

        self.model = TranscriptModel(session)
        self.list = TranscriptList()
        self.list.setObjectName("transcriptList")
        self.list.setModel(self.model)
        self.list.setItemDelegate(TranscriptDelegate(session, self.list))
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(False)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.verticalScrollBar().valueChanged.connect(self._on_scroll)
        root.addWidget(self.list, 1)

        self.partial = QLabel("")
        self.partial.setObjectName("partialLabel")
        self.partial.setWordWrap(True)
        self.partial.setVisible(False)
        root.addWidget(self.partial)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_clock(self, seconds: float) -> None:
        self.clock_label.setText(format_timestamp(seconds))

    def set_level(self, rms: float) -> None:
        self.meter.set_level(rms)

    def reload(self) -> None:
        self.model.reload()
        self._sync_empty()
        if self._stick:
            self.list.scrollToBottom()

    def append_segment(self) -> None:
        self.model.append_row()
        self._sync_empty()
        if self._stick:
            self.list.scrollToBottom()

    def refresh_names(self) -> None:
        self.model.refresh_visible()
        self.list.scheduleDelayedItemsLayout()

    def set_partial(self, text: str, seconds: float, speaker_name: str | None) -> None:
        if not text:
            self.partial.clear()
            self.partial.setVisible(False)
        else:
            timestamp = format_timestamp(seconds)
            if speaker_name:
                shown = f"[{timestamp}] {speaker_name}：{text}"
            else:
                shown = f"[{timestamp}] （识别中）：{text}"
            self.partial.setText(shown)
            self.partial.setVisible(True)
        self._sync_empty()

    def plain_text(self) -> str:
        lines = [self.session.format_line(segment) for segment in self.session.segments]
        if self.partial.text():
            lines.append(self.partial.text())
        return "\n".join(lines)

    def _sync_empty(self) -> None:
        visible = not self.session.segments and not self.partial.text()
        self.empty_label.setVisible(visible)

    def _on_scroll(self, value: int) -> None:
        bar = self.list.verticalScrollBar()
        self._stick = value >= bar.maximum() - 4

    def _menu(self, pos) -> None:
        index = self.list.indexAt(pos)
        if not index.isValid():
            return
        segment = self.session.segments[index.row()]
        menu = QMenu(self)
        submenu = menu.addMenu("改为发言人")
        for speaker_id, speaker in self.session.speakers.items():
            action = submenu.addAction(speaker.name)
            action.setEnabled(speaker_id != segment.speaker_id)
            action.triggered.connect(
                lambda checked=False, idx=segment.idx, sid=speaker_id: self.reassign_requested.emit(idx, sid)
            )
        menu.exec(self.list.viewport().mapToGlobal(pos))
