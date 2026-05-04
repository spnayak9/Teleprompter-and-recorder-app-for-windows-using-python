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
    QGroupBox,
    QLabel,
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

        self.recording_camera = QComboBox()
        self.preview_camera = QComboBox()
        self.audio_device = QComboBox()

        form.addRow("Recording Camera", self.recording_camera)
        form.addRow("Preview Camera", self.preview_camera)
        form.addRow("Microphone", self.audio_device)

        self.recording_camera.currentIndexChanged.connect(self._on_camera_changed)

    def _build_video_tab(self) -> None:
        main_layout = QVBoxLayout(self.video_tab)
        
        # Capture settings
        capture_group = QGroupBox("Capture Source")
        capture_form = QFormLayout(capture_group)
        self.resolution = QComboBox()
        self.fps = QComboBox()
        self.pixel_format = QComboBox()
        capture_form.addRow("Resolution", self.resolution)
        capture_form.addRow("FPS", self.fps)
        capture_form.addRow("Pixel format", self.pixel_format)
        main_layout.addWidget(capture_group)

        # Encoding Settings
        encoding_group = QGroupBox("Video Encoding")
        self.encoding_form = QFormLayout(encoding_group)
        
        self.encoder_type = QComboBox()
        self.encoder_type.addItem("Camera Stream Copy (Recommended for 4K)", "copy")
        self.encoder_type.addItem("Software Encoding (CPU)", "software")
        self.encoder_type.addItem("Hardware Encoding (GPU)", "hardware")
        
        self.software_codec = QComboBox()
        self.software_codec.addItem("H.264 High Quality CPU", "libx264_hq")
        self.software_codec.addItem("H.264 Lossless CPU (High Risk)", "libx264_lossless")
        self.software_codec.addItem("FFV1 Lossless CPU (Archival)", "ffv1")
        self.software_codec.addItem("MJPEG Software", "mjpeg")
        
        self.hardware_codec = QComboBox()
        
        self.quality_preset = QComboBox()
        self.quality_preset.addItem("High Quality (Balanced)", "hq")
        self.quality_preset.addItem("Visually Lossless", "visually_lossless")
        self.quality_preset.addItem("Maximum Performance", "fast")
        
        self.encoding_form.addRow("Encoding Mode", self.encoder_type)
        self.encoding_form.addRow("Software Codec", self.software_codec)
        self.encoding_form.addRow("Hardware Codec", self.hardware_codec)
        self.encoding_form.addRow("Quality Preset", self.quality_preset)
        
        # Help Label
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        self.help_label.setStyleSheet("color: #888; font-size: 11px;")
        self.encoding_form.addRow(self.help_label)
        
        main_layout.addWidget(encoding_group)
        main_layout.addStretch()

        self.resolution.currentIndexChanged.connect(self._on_resolution_changed)
        self.fps.currentIndexChanged.connect(self._on_fps_changed)
        self.encoder_type.currentIndexChanged.connect(self._on_encoding_mode_changed)
        self._on_encoding_mode_changed()
    
    def _on_encoding_mode_changed(self) -> None:
        mode = self.encoder_type.currentData()
        
        self.software_codec.setEnabled(mode == "software")
        self.hardware_codec.setEnabled(mode == "hardware")
        self.quality_preset.setEnabled(mode != "copy")
        
        if mode == "copy":
            self.help_label.setText("<b>Stream Copy</b>: Preserves camera's raw feed without re-encoding. Best for 4K stability.")
        elif mode == "software":
            self.help_label.setText("<b>Software</b>: High compatibility, but very heavy at 4K. May drop frames.")
        elif mode == "hardware":
            self.help_label.setText("<b>Hardware</b>: Low CPU usage. Uses GPU for smooth real-time encoding.")

    def _build_output_tab(self) -> None:
        form = QFormLayout(self.output_tab)

        self.container = QComboBox()
        self.output_dir = QLineEdit()
        browse = QPushButton("Browse")
        browse.clicked.connect(self._choose_output_dir)

        self.recording_sample_rate = QComboBox()
        self.recording_channels = QComboBox()
        self.audio_codec = QComboBox()
        self.audio_bitrate = QComboBox()

        for sr in [16000, 32000, 44100, 48000]:
            self.recording_sample_rate.addItem(f"{sr} Hz", sr)
        
        self.recording_channels.addItem("Mono", 1)
        self.recording_channels.addItem("Stereo", 2)

        self.audio_codec.addItem("FLAC lossless", "flac")
        self.audio_codec.addItem("MP3", "libmp3lame")
        self.audio_codec.addItem("WAV PCM 16-bit", "pcm_s16le")
        self.audio_codec.addItem("AAC", "aac")
        self.audio_codec.addItem("Opus", "libopus")

        self.audio_bitrate.addItem("Lossless / Auto", "")
        for br in ["128k", "192k", "256k", "320k"]:
            self.audio_bitrate.addItem(br, br)

        form.addRow("Container", self.container)
        form.addRow("Output directory", self.output_dir)
        form.addRow("", browse)
        form.addRow("Audio Sample Rate", self.recording_sample_rate)
        form.addRow("Audio Channels", self.recording_channels)
        form.addRow("Audio Codec", self.audio_codec)
        form.addRow("Audio Bitrate", self.audio_bitrate)

    def _populate_from_profile(self) -> None:
        self.recording_camera.clear()
        self.preview_camera.clear()
        self.audio_device.clear()
        self.container.clear()

        self.preview_camera.addItem("Same as Recording Camera", "__same_as_recording__")
        for cam in self.system_profile.cameras:
            self.recording_camera.addItem(cam.name, cam.ffmpeg_name)
            self.preview_camera.addItem(cam.name, cam.ffmpeg_name)

        for mic in self.system_profile.audio_inputs:
            self.audio_device.addItem(mic.name, mic.ffmpeg_name)

        self.hardware_codec.clear()
        if not self.system_profile.hardware_video_encoders:
            self.hardware_codec.addItem("No hardware encoders detected", None)
        else:
            for enc in self.system_profile.hardware_video_encoders:
                from teleprompter_app.system_probe import HARDWARE_ENCODERS
                label = HARDWARE_ENCODERS.get(enc, enc)
                self.hardware_codec.addItem(label, enc)

        for muxer in self.system_profile.containers:
            self.container.addItem(muxer, muxer)

        self._on_camera_changed()

    def _selected_camera(self) -> CameraProfile | None:
        ffmpeg_name = self.recording_camera.currentData()
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

            if self.fps.count() > 0:
                self.fps.setCurrentIndex(0)

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
        # Compatibility migration
        rec_cam = self.settings.recording_video_device or self.settings.video_device
        pre_cam = self.settings.preview_video_device or "__same_as_recording__"

        self._set_combo_by_data(self.recording_camera, rec_cam)
        self._set_combo_by_data(self.preview_camera, pre_cam)
        self._on_camera_changed()

        self._set_combo_by_data(self.resolution, self.settings.resolution)
        self._on_resolution_changed()

        try:
            fps_val = float(self.settings.fps)
            self._set_combo_by_data(self.fps, fps_val)
        except (ValueError, TypeError):
            pass
        self._on_fps_changed()

        # Encoding Mode & Codecs
        self._set_combo_by_data(self.encoder_type, self.settings.video_encoder_type)
        self._set_combo_by_data(self.software_codec, self.settings.software_encoder or self.settings.video_codec_mode)
        self._set_combo_by_data(self.hardware_codec, self.settings.hardware_encoder)
        self._set_combo_by_data(self.quality_preset, self.settings.quality_preset)
        self._on_encoding_mode_changed()

        self._set_combo_by_data(self.audio_device, self.settings.audio_device)
        self._set_combo_by_data(self.container, self.settings.container)
        
        self._set_combo_by_data(self.recording_sample_rate, self.settings.recording_sample_rate)
        self._set_combo_by_data(self.recording_channels, self.settings.recording_channels)
        self._set_combo_by_data(self.audio_codec, self.settings.audio_codec)
        self._set_combo_by_data(self.audio_bitrate, self.settings.audio_bitrate)

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
        current = self.settings
        
        try:
            fps_val = int(float(self.fps.currentData() or 30))
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
            "recording_video_device": self.recording_camera.currentData() or "",
            "preview_video_device": self.preview_camera.currentData() or "__same_as_recording__",
            "video_device": self.recording_camera.currentData() or "", # Compatibility
            "audio_device": self.audio_device.currentData() or "",
            "resolution": self.resolution.currentData() or "",
            "fps": fps_val,
            "pixel_format": fmt_name,
            "input_format_kind": fmt_kind,
            
            # New Encoding Settings
            "video_encoder_type": self.encoder_type.currentData(),
            "software_encoder": self.software_codec.currentData(),
            "hardware_encoder": self.hardware_codec.currentData(),
            "quality_preset": self.quality_preset.currentData(),
            "video_codec_mode": self.software_codec.currentData() if self.encoder_type.currentData() == "software" else self.encoder_type.currentData(),
            
            "container": self.container.currentData() or "",
            "output_dir": self.output_dir.text().strip(),
            "recording_sample_rate": int(self.recording_sample_rate.currentData() or 48000),
            "recording_channels": int(self.recording_channels.currentData() or 1),
            "audio_codec": self.audio_codec.currentData() or "flac",
            "audio_bitrate": self.audio_bitrate.currentData() or "",
        }

        settings = current.updated(updates)
        ConfigManager().save(settings)
        self.saved.emit(settings)
        self.accept()
