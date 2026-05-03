"""Enhanced main view components: preview overlay and recording controls.

This module provides a `PreviewOverlay` widget that places a live camera
preview behind a teleprompter widget, and a `RecordingControls` widget
that exposes recording mode selection and start/stop buttons.

These are intended to be integrated into the existing `MainWindow` by the
application wiring code (they are provided as reusable components).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QImage, QColor, QFont
from PySide6.QtWidgets import (
    QLabel,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QComboBox,
    QGroupBox,
)
import numpy as np


class PreviewOverlay(QWidget):
    def __init__(self, teleprompter_widget: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.teleprompter = teleprompter_widget
        self.preview_label = QLabel(self)
        self.preview_label.setScaledContents(True)
        self.preview_label.setSizePolicy(self.teleprompter.sizePolicy())
        self.preview_label.hide()
        # let mouse events pass through to the teleprompter widget
        try:
            self.preview_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            # ensure preview is stacked under the teleprompter
            self.preview_label.stackUnder(self.teleprompter)
        except Exception:
            pass

        self.fps_label = QLabel(self)
        self.fps_label.setStyleSheet("color: #00ff00; background: rgba(0,0,0,0.5); padding: 4px;")
        self.fps_label.setFont(QFont("monospace", 10))
        self.fps_label.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # Teleprompter is managed by the layout; preview_label is positioned
        # absolutely as a background child and must not be part of the layout
        layout.addWidget(self.teleprompter)
        # ensure preview is below the teleprompter
        try:
            self.preview_label.lower()
            self.teleprompter.raise_()
        except Exception:
            pass
        self._preview_enabled = False
        self._background_color = "#000000"

    def enable_preview(self, enabled: bool) -> None:
        self._preview_enabled = bool(enabled)
        if not self._preview_enabled:
            self.preview_label.hide()
            self.fps_label.hide()
        else:
            # show current background color until frames arrive
            self.set_background_color(self._background_color)

    def set_background_color(self, color: str) -> None:
        self._background_color = color
        # create a solid color pixmap as fallback background
        try:
            pix = QPixmap(self.size())
            pix.fill(QColor(color))
            self.preview_label.setPixmap(pix)
            if not self.preview_label.isVisible():
                self.preview_label.show()
        except Exception:
            pass

    def set_frame(self, frame: np.ndarray) -> None:
        # frame expected in BGR (OpenCV) format
        rgb = frame[:, :, ::-1]
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        image = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(image)
        self.preview_label.setPixmap(pix)
        if not self.preview_label.isVisible():
            self.preview_label.show()
            try:
                # ensure preview_label stays behind teleprompter content
                self.preview_label.lower()
                self.teleprompter.raise_()
            except Exception:
                pass
            self.fps_label.show()

    def set_fps(self, fps: float) -> None:
        self.fps_label.setText(f"FPS: {fps:.1f}")
        # position top-right
        self.fps_label.adjustSize()
        self.fps_label.move(self.width() - self.fps_label.width() - 12, 8)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        # Preview label fills the widget as absolute background
        self.preview_label.setGeometry(0, 0, self.width(), self.height())
        # teleprompter is laid out by the layout; no absolute resize required
        self.fps_label.move(self.width() - self.fps_label.width() - 12, 8)


class RecordingControls(QGroupBox):
    def __init__(self, parent=None) -> None:
        super().__init__("Recording")
        self.mode = QComboBox()
        options = [
            "record only srt",
            "record only audio",
            "record only video",
            "audio with srt",
            "video with srt",
            "audio and video only",
            "audio + video + srt",
        ]
        for o in options:
            self.mode.addItem(o)

        self.start_btn = QPushButton("Start Recording")
        self.stop_btn = QPushButton("Stop Recording")
        self.stop_btn.setEnabled(False)

        layout = QHBoxLayout(self)
        layout.addWidget(self.mode)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.stop_btn)
        self.mode_label = QLabel("")
        layout.addWidget(self.mode_label)

    def set_selected_mode(self, text: str) -> None:
        self.mode_label.setText(text)
