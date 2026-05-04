"""Enhanced main view components: preview overlay and recording controls.

This module provides a `PreviewOverlay` widget that places a live camera
preview behind a teleprompter widget, and a `RecordingControls` widget
that exposes recording mode selection and start/stop buttons.

These are intended to be integrated into the existing `MainWindow` by the
application wiring code (they are provided as reusable components).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal, QSignalBlocker
from PySide6.QtGui import QPixmap, QImage, QColor, QFont
from PySide6.QtWidgets import (
    QLabel,
    QWidget,
    QHBoxLayout,
    QPushButton,
    QComboBox,
    QGroupBox,
    QStackedLayout,
)
import numpy as np


class PreviewOverlay(QWidget):
    def __init__(self, teleprompter_widget: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.teleprompter = teleprompter_widget
        self.preview_label = QLabel()
        self.preview_label.setScaledContents(True)
        self.preview_label.setSizePolicy(self.teleprompter.sizePolicy())
        self.preview_label.hide()
        
        # let mouse events pass through to the teleprompter widget
        try:
            self.preview_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        except Exception:
            pass

        self.fps_label = QLabel(self)
        self.fps_label.setStyleSheet("color: #00ff00; background: rgba(0,0,0,0.5); padding: 4px;")
        self.fps_label.setFont(QFont("monospace", 10))
        self.fps_label.hide()

        self.paused_label = QLabel("Preview paused during video recording", self)
        self.paused_label.setStyleSheet("color: #ffffff; background: rgba(255,0,0,0.6); padding: 10px; border-radius: 4px;")
        self.paused_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.paused_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.paused_label.hide()

        # Use StackedLayout to layer the teleprompter over the background
        self._stacked_layout = QStackedLayout(self)
        self._stacked_layout.setStackingMode(QStackedLayout.StackAll)
        self._stacked_layout.setContentsMargins(0, 0, 0, 0)
        
        # Index 0: background (preview), Index 1: foreground (teleprompter)
        self._stacked_layout.addWidget(self.preview_label)
        self._stacked_layout.addWidget(self.teleprompter)
        self._stacked_layout.setCurrentIndex(1) # Bring teleprompter to front

        self._preview_enabled = False
        self._background_color = "#000000"

    def enable_preview(self, enabled: bool) -> None:
        self._preview_enabled = bool(enabled)
        if not self._preview_enabled:
            self.preview_label.hide()
            self.fps_label.hide()
            self.paused_label.hide()
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

    def set_frame(self, image: QImage) -> None:
        # image expected as QImage (already converted to RGB)
        self.paused_label.hide()
        pix = QPixmap.fromImage(image)
        self.preview_label.setPixmap(pix)
        if not self.preview_label.isVisible():
            self.preview_label.show()
            self.fps_label.show()

    def set_preview_paused(self, paused: bool, message: str = "") -> None:
        if message:
            self.paused_label.setText(message)
        
        if paused:
            self.paused_label.show()
            self.fps_label.hide()
            self._update_paused_pos()
        else:
            self.paused_label.hide()

    def clear_preview_frame(self) -> None:
        # Show black background or current background color
        self.set_background_color(self._background_color)
        self.fps_label.hide()

    def _update_paused_pos(self) -> None:
        self.paused_label.adjustSize()
        x = (self.width() - self.paused_label.width()) // 2
        y = (self.height() - self.paused_label.height()) // 2
        self.paused_label.move(x, y)

    def set_fps(self, fps: float) -> None:
        self.fps_label.setText(f"FPS: {fps:.1f}")
        # position top-right
        self.fps_label.adjustSize()
        self.fps_label.move(self.width() - self.fps_label.width() - 12, 8)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self.fps_label.move(self.width() - self.fps_label.width() - 12, 8)
        self._update_paused_pos()


class MainToolbarControls(QWidget):
    start_recording_requested = Signal()
    stop_recording_requested = Signal()
    mode_changed = Signal(str)
    background_changed = Signal(str)
    preview_resolution_changed = Signal(str)
    preview_camera_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.mode = QComboBox()
        self.mode.addItem("SRT only", "srt only")
        self.mode.addItem("Audio only", "audio only")
        self.mode.addItem("Video only", "video only")
        self.mode.addItem("Audio + SRT", "audio + srt")
        self.mode.addItem("Video + SRT", "video + srt")
        self.mode.addItem("Audio + Video", "audio + video")
        self.mode.addItem("Audio + Video + SRT", "audio + video + srt")
            
        self.background_selector = QComboBox()
        self.background_selector.addItem("Color", "color")
        self.background_selector.addItem("Camera Preview", "camera")
        
        self.preview_res_selector = QComboBox()
        self.preview_res_selector.addItem("240p", "240p")
        self.preview_res_selector.addItem("360p", "360p")
        self.preview_res_selector.addItem("480p", "480p")
        self.preview_res_selector.addItem("720p", "720p")
        self.preview_res_selector.setCurrentText("360p")

        self.preview_camera_selector = QComboBox()
        self.preview_camera_selector.addItem("Same as Recording", "__same_as_recording__")

        self.start_btn = QPushButton("Start Recording")
        self.stop_btn = QPushButton("Stop Recording")
        self.stop_btn.setEnabled(False)

        layout.addWidget(QLabel("Mode:"))
        layout.addWidget(self.mode)
        layout.addWidget(QLabel("Bg:"))
        layout.addWidget(self.background_selector)
        layout.addWidget(QLabel("Res:"))
        layout.addWidget(self.preview_res_selector)
        layout.addWidget(QLabel("Cam:"))
        layout.addWidget(self.preview_camera_selector)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.stop_btn)
        
        # Connect internal signals
        self.start_btn.clicked.connect(lambda _checked=False: self.start_recording_requested.emit())
        self.stop_btn.clicked.connect(lambda _checked=False: self.stop_recording_requested.emit())
        self.mode.currentIndexChanged.connect(
            lambda _i: self.mode_changed.emit(self.mode.currentData())
        )
        self.background_selector.currentIndexChanged.connect(lambda _idx: self.background_changed.emit(str(self.background_selector.currentData())))
        self.preview_res_selector.currentTextChanged.connect(self.preview_resolution_changed.emit)
        self.preview_camera_selector.currentIndexChanged.connect(
            lambda _idx: self.preview_camera_changed.emit(self.preview_camera_selector.currentData())
        )

    def populate_preview_cameras(self, cameras) -> None:
        with QSignalBlocker(self.preview_camera_selector):
            self.preview_camera_selector.clear()
            self.preview_camera_selector.addItem("Same as Recording", "__same_as_recording__")
            for cam in cameras:
                self.preview_camera_selector.addItem(cam.name, cam.ffmpeg_name)

    def current_preview_camera(self) -> str:
        return self.preview_camera_selector.currentData() or "__same_as_recording__"

    def set_recording_state(self, is_recording: bool) -> None:
        self.start_btn.setEnabled(not is_recording)
        self.stop_btn.setEnabled(is_recording)
        self.mode.setEnabled(not is_recording)

    def current_recording_mode(self) -> str:
        return self.mode.currentData() or "srt only"
