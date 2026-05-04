from __future__ import annotations

from pathlib import Path
import logging

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from teleprompter_app.utils.config import ConfigManager, AppSettings
from teleprompter_app.system_profile import CameraProfile, SystemProfile

logger = logging.getLogger(__name__)

class ConfigDialog(QDialog):
    saved = Signal(AppSettings)

    def __init__(
        self,
        system_profile: SystemProfile,
        config_path: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("Recording Configuration")
        self.system_profile = system_profile
        self.manager = ConfigManager(config_path)
        self.settings = self.manager.load()
        self._build_ui()
        self._populate_from_profile()
        self._restore_settings()

    def _build_ui(self) -> None:
        self.tabs = QTabWidget(self)

        self.device_tab = QWidget()
        self.video_tab = QWidget()
        self.output_tab = QWidget()

        self.tabs.addTab(self.device_tab, "Device")
        self.tabs.addTab(self.video_tab, "Video")
        self.tabs.addTab(self.output_tab, "Output")

        self._build_device_tab()
        self._build_video_tab()
        self._build_output_tab()

        self.save_btn = QPushButton("Save")
        self.cancel_btn = QPushButton("Cancel")

        self.save_btn.clicked.connect(self._save)
        self.cancel_btn.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(self.save_btn)
        layout.addWidget(self.cancel_btn)

    def _build_device_tab(self) -> None:
        form = QFormLayout(self.device_tab)

        self.video_device = QComboBox()
        self.audio_device = QComboBox()

        form.addRow("Camera", self.video_device)
        form.addRow("Microphone", self.audio_device)

        self.video_device.currentIndexChanged.connect(self._on_camera_changed)

    def _build_video_tab(self) -> None:
        form = QFormLayout(self.video_tab)

        self.resolution = QComboBox()
        self.fps = QComboBox()
        self.pixel_format = QComboBox()
        self.video_codec = QComboBox()
        self.lossless = QCheckBox()

        form.addRow("Resolution", self.resolution)
        form.addRow("FPS", self.fps)
        form.addRow("Pixel format", self.pixel_format)
        form.addRow("Video codec", self.video_codec)
        form.addRow("Lossless", self.lossless)

        self.resolution.currentIndexChanged.connect(self._on_resolution_changed)
        self.fps.currentIndexChanged.connect(self._on_fps_changed)
        self.pixel_format.currentIndexChanged.connect(self._on_format_changed)

    def _build_output_tab(self) -> None:
        form = QFormLayout(self.output_tab)

        self.container = QComboBox()
        self.output_dir = QLineEdit()
        browse = QPushButton("Browse")
        browse.clicked.connect(self._choose_output_dir)

        form.addRow("Container", self.container)
        form.addRow("Output directory", self.output_dir)
        form.addRow("", browse)

    def _populate_from_profile(self) -> None:
        self.video_device.clear()
        self.audio_device.clear()
        self.video_codec.clear()
        self.container.clear()

        for cam in self.system_profile.cameras:
            self.video_device.addItem(cam.name, cam.ffmpeg_name)

        for mic in self.system_profile.audio_inputs:
            self.audio_device.addItem(mic.name, mic.ffmpeg_name)

        for codec in self.system_profile.video_codecs:
            self.video_codec.addItem(codec, codec)

        for muxer in self.system_profile.containers:
            self.container.addItem(muxer, muxer)

        self.lossless.setChecked(self.settings.lossless)

        self._on_camera_changed()

    def _selected_camera(self) -> CameraProfile | None:
        ffmpeg_name = self.video_device.currentData()
        if not ffmpeg_name:
            return None
        return self.system_profile.camera_by_ffmpeg_name(ffmpeg_name)

    def _on_camera_changed(self) -> None:
        """
        Pure UI cascade:
        Camera -> Resolution.
        """
        cam = self._selected_camera()

        with QSignalBlocker(self.resolution), QSignalBlocker(self.fps), QSignalBlocker(self.pixel_format):
            self.resolution.clear()
            self.fps.clear()
            self.pixel_format.clear()

            if cam is None:
                return

            resolutions = sorted(
                {mode.resolution for mode in cam.formats},
                key=lambda r: (
                    int(r.split("x")[0]) * int(r.split("x")[1]),
                    int(r.split("x")[0]),
                ),
                reverse=True,
            )

            for res in resolutions:
                self.resolution.addItem(res, res)

        self._on_resolution_changed()

    def _on_resolution_changed(self) -> None:
        """
        Pure UI cascade:
        Resolution -> FPS.
        """
        cam = self._selected_camera()
        resolution = self.resolution.currentData()

        with QSignalBlocker(self.fps), QSignalBlocker(self.pixel_format):
            self.fps.clear()
            self.pixel_format.clear()

            if cam is None or not resolution:
                return

            fps_values = sorted(
                {
                    float(mode.fps)
                    for mode in cam.formats
                    if mode.resolution == resolution
                },
                reverse=True,
            )

            for fps in fps_values:
                label = str(int(fps)) if fps.is_integer() else f"{fps:g}"
                self.fps.addItem(label, fps)

        self._on_fps_changed()

    def _on_fps_changed(self) -> None:
        """
        Pure UI cascade:
        FPS -> Pixel format.
        """
        cam = self._selected_camera()
        resolution = self.resolution.currentData()
        fps = self.fps.currentData()

        with QSignalBlocker(self.pixel_format):
            self.pixel_format.clear()

            if cam is None or not resolution or fps is None:
                return

            formats = sorted(
                {
                    (mode.format_name, mode.format_kind)
                    for mode in cam.formats
                    if mode.resolution == resolution and float(mode.fps) == float(fps)
                }
            )

            for fmt_name, fmt_kind in formats:
                self.pixel_format.addItem(
                    fmt_name,
                    {
                        "format_name": fmt_name,
                        "format_kind": fmt_kind,
                    },
                )

        self._on_format_changed()

    def _on_format_changed(self) -> None:
        return

    def _restore_settings(self) -> None:
        self._set_combo_by_data(self.video_device, self.settings.video_device)
        self._on_camera_changed()

        self._set_combo_by_data(self.resolution, self.settings.resolution)
        self._on_resolution_changed()

        try:
            self._set_combo_by_data(self.fps, float(self.settings.fps))
        except (ValueError, TypeError):
            pass
        self._on_fps_changed()

        self._set_combo_by_data(self.pixel_format, self.settings.pixel_format)
        self._set_combo_by_data(self.video_codec, self.settings.video_codec)
        self._set_combo_by_data(self.audio_device, self.settings.audio_device)
        self._set_combo_by_data(self.container, self.settings.container)

        self.output_dir.setText(self.settings.output_dir)

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, value) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    def _choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select output directory")
        if directory:
            self.output_dir.setText(directory)

    def _save(self) -> None:
        """
        Save must only persist values and close dialog.
        """
        current = self.manager.load()
        
        try:
            fps_val = int(float(self.fps.currentData() or 0))
        except (ValueError, TypeError):
            fps_val = 30

        fmt_data = self.pixel_format.currentData() or {}
        if isinstance(fmt_data, dict):
            fmt_name = fmt_data.get("format_name", "")
            fmt_kind = fmt_data.get("format_kind", "pixel_format")
        else:
            fmt_name = str(fmt_data or "")
            fmt_kind = "pixel_format"

        updates = {
            "video_device": self.video_device.currentData() or "",
            "audio_device": self.audio_device.currentData() or "",
            "resolution": self.resolution.currentData() or "",
            "fps": fps_val,
            "pixel_format": fmt_name,
            "input_format_kind": fmt_kind,
            "video_codec": self.video_codec.currentData() or "",
            "lossless": self.lossless.isChecked(),
            "container": self.container.currentData() or "",
            "output_dir": self.output_dir.text().strip(),
        }

        settings = current.updated(updates)
        self.manager.save(settings)
        self.saved.emit(settings)
        self.accept()
