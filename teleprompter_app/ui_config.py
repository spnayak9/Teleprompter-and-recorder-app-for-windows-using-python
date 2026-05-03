"""Configuration dialog with tabs for device, video, audio, performance, and output.

Lightweight PySide6 dialog that persists settings using `ConfigManager`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QPushButton,
    QFileDialog,
)

from teleprompter_app.config_manager import ConfigManager, RecorderSettings


class ConfigDialog(QDialog):
    saved = Signal(object)

    def __init__(self, config_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recording Configuration")
        self.manager = ConfigManager(config_path)
        self.settings = self.manager.load()
        self._build_ui()

    def _build_ui(self) -> None:
        self.tabs = QTabWidget()

        self._device_tab = QWidget()
        self._video_tab = QWidget()
        self._audio_tab = QWidget()
        self._perf_tab = QWidget()
        self._advanced_tab = QWidget()
        self._output_tab = QWidget()

        self.tabs.addTab(self._device_tab, "Device")
        self.tabs.addTab(self._video_tab, "Video")
        self.tabs.addTab(self._audio_tab, "Audio")
        self.tabs.addTab(self._perf_tab, "Performance")
        self.tabs.addTab(self._advanced_tab, "Advanced")
        self.tabs.addTab(self._output_tab, "Output")

        self._build_device_tab()
        self._build_video_tab()
        self._build_audio_tab()
        self._build_perf_tab()
        self._build_advanced_tab()
        self._build_output_tab()

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(save_btn)
        layout.addWidget(cancel_btn)

    def _build_device_tab(self) -> None:
        form = QFormLayout(self._device_tab)
        self.video_device = QLineEdit(self.settings.video_device)
        self.audio_device = QLineEdit(self.settings.audio_device)
        browse_cam = QPushButton("Browse")
        browse_cam.clicked.connect(lambda: self._choose_dir(self.video_device))
        form.addRow("Camera device", self.video_device)
        form.addRow("Microphone device", self.audio_device)

    def _choose_dir(self, line: QLineEdit) -> None:
        # placeholder: device selection could be improved to query DirectShow
        path = QFileDialog.getExistingDirectory(self, "Select device (placeholder)")
        if path:
            line.setText(path)

    def _build_video_tab(self) -> None:
        form = QFormLayout(self._video_tab)
        self.resolution = QLineEdit(self.settings.resolution)
        self.fps = QSpinBox()
        self.fps.setRange(1, 240)
        self.fps.setValue(self.settings.fps)
        self.video_codec = QLineEdit(self.settings.video_codec)
        self.lossless = QCheckBox()
        self.lossless.setChecked(self.settings.lossless)
        form.addRow("Resolution", self.resolution)
        form.addRow("FPS", self.fps)
        form.addRow("Video codec", self.video_codec)
        form.addRow("Lossless", self.lossless)

    def _build_audio_tab(self) -> None:
        form = QFormLayout(self._audio_tab)
        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 192000)
        self.sample_rate.setValue(self.settings.sample_rate)
        self.channels = QSpinBox()
        self.channels.setRange(1, 8)
        self.channels.setValue(self.settings.channels)
        self.audio_codec = QLineEdit(self.settings.audio_codec)
        form.addRow("Sample rate", self.sample_rate)
        form.addRow("Channels", self.channels)
        form.addRow("Audio codec", self.audio_codec)

    def _build_perf_tab(self) -> None:
        form = QFormLayout(self._perf_tab)
        self.rtbuf = QLineEdit(self.settings.rtbufsize)
        self.thread_q = QSpinBox()
        self.thread_q.setRange(1, 32768)
        self.thread_q.setValue(self.settings.thread_queue_size)
        self.hw_accel = QCheckBox()
        self.hw_accel.setChecked(self.settings.hw_accel)
        form.addRow("Buffer size", self.rtbuf)
        form.addRow("Thread queue size", self.thread_q)
        form.addRow("Hardware accel", self.hw_accel)

    def _build_advanced_tab(self) -> None:
        form = QFormLayout(self._advanced_tab)
        self.extra_args = QLineEdit(self.settings.extra_ffmpeg_args)
        form.addRow("Extra ffmpeg args", self.extra_args)

    def _build_output_tab(self) -> None:
        form = QFormLayout(self._output_tab)
        self.container = QLineEdit(self.settings.container)
        self.output_dir = QLineEdit(self.settings.output_dir)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._choose_output_dir)
        form.addRow("Container", self.container)
        form.addRow("Output dir", self.output_dir)
        form.addRow("", browse)

    def _choose_output_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select output directory")
        if d:
            self.output_dir.setText(d)

    def _save(self) -> None:
        s = RecorderSettings(
            video_device=self.video_device.text().strip(),
            audio_device=self.audio_device.text().strip(),
            resolution=self.resolution.text().strip(),
            fps=int(self.fps.value()),
            pixel_format="yuv420p",
            video_codec=self.video_codec.text().strip() or self.settings.video_codec,
            lossless=bool(self.lossless.isChecked()),
            sample_rate=int(self.sample_rate.value()),
            channels=int(self.channels.value()),
            audio_codec=self.audio_codec.text().strip() or self.settings.audio_codec,
            rtbufsize=self.rtbuf.text().strip() or self.settings.rtbufsize,
            thread_queue_size=int(self.thread_q.value()),
            hw_accel=bool(self.hw_accel.isChecked()),
            container=self.container.text().strip() or self.settings.container,
            output_dir=self.output_dir.text().strip() or self.settings.output_dir,
            naming_pattern=self.settings.naming_pattern,
            extra_ffmpeg_args=self.extra_args.text().strip(),
        )

        self.manager.save(s)
        self.saved.emit(s)
        self.accept()


__all__ = ["ConfigDialog"]
