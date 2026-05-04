"""Application entry point for AI Teleprompter."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from teleprompter_app.app import TeleprompterController
from teleprompter_app.utils.logger import configure_logging


def main() -> int:
    """Create the Qt application and launch the main controller."""

    configure_logging()
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("AI Teleprompter with Real-Time Speech Highlighting")
    qt_app.setOrganizationName("Teleprompter App")

    controller = TeleprompterController(qt_app)
    controller.show()

    code = qt_app.exec()
    controller.shutdown()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
