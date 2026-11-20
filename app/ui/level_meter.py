"""Small live RMS meter shown next to the meeting clock."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget


class LevelMeter(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(78, 22)
        self._level = 0.0
        self.setToolTip("麦克风音量")

    def set_level(self, rms: float) -> None:
        scaled = max(0.0, min(1.0, float(rms) / 0.08))
        self._level = self._level * 0.55 + scaled * 0.45
        if self._level < 0.02:
            self._level = 0.0
        self.update()

    def reset(self) -> None:
        self._level = 0.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        bars = 8
        gap = 2
        width = (self.width() - gap * (bars - 1)) / bars
        active = int(round(self._level * bars))
        for index in range(bars):
            if index < active and index >= 6:
                color = QColor("#F9A825")
            elif index < active:
                color = QColor("#2E7D32")
            else:
                color = QColor("#D0D0D0")
            painter.fillRect(
                int(index * (width + gap)),
                2,
                max(1, int(width)),
                self.height() - 4,
                color,
            )
        painter.end()
