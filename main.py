"""会议纪要 entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def _prepare_path() -> None:
    if getattr(sys, "frozen", False):
        return
    root = str(Path(__file__).resolve().parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def configure_logging() -> None:
    try:
        from app.utils.paths import data_dir

        directory = data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        log_path = directory / "meeting_minutes.log"
        handlers: list[logging.Handler] = [
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ]
    except Exception:
        handlers = [logging.StreamHandler()]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> int:
    _prepare_path()
    configure_logging()
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow
    from app.utils.paths import resource_path

    app = QApplication(sys.argv)
    app.setApplicationName("会议纪要")
    app.setOrganizationName("MeetingMinutes")
    font = QFont()
    font.setFamilies(["PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", "sans-serif"])
    font.setPointSize(13)
    app.setFont(font)
    qss = resource_path("app", "ui", "styles.qss")
    if qss.is_file():
        app.setStyleSheet(qss.read_text(encoding="utf-8"))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
