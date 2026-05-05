from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from teleprompter_app.utils.config import ConfigManager, AppSettings, SubtitleTimingMode
from teleprompter_app.system_profile import CameraProfile, SystemProfile, EncoderState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Preset definitions
# ---------------------------------------------------------------------------

PRESETS = {
    "stable_4k_feed": {
        "label": "Stable 4K Camera Feed",
        "video_encoder_type": "copy",
        "video_codec_mode": "copy",
        "software_encoder": "",
        "hardware_encoder": "",
        "container": "mkv",
        "audio_codec": "flac",
        "audio_bitrate": "",
        "description": "Stream Copy + FLAC. Preserves raw camera MJPEG stream. No re-encoding CPU load. Best choice for 4K.",
    },
    "windows_friendly": {
        "label": "Windows Friendly H.264",
        "video_encoder_type": "software",
        "video_codec_mode": "standard",
        "software_encoder": "libx264",
        "hardware_encoder": "",
        "container": "mp4",
        "audio_codec": "aac",
        "audio_bitrate": "192k",
        "description": "H.264 MP4 + AAC. Maximum compatibility for Windows Media Player and sharing. Heavy on CPU at 4K.",
    },
    "hardware_hq": {
        "label": "Hardware High Quality",
        "video_encoder_type": "hardware",
        "video_codec_mode": "hardware_hq",
        "software_encoder": "",
        "hardware_encoder": "__best__",  # resolved at save time
        "container": "mkv",
        "audio_codec": "flac",
        "audio_bitrate": "",
        "description": "GPU-accelerated H.264 + FLAC. Low CPU usage. Only available if a usable hardware encoder is detected.",
    },
    "archival_lossless": {
        "label": "Archival Lossless",
        "video_encoder_type": "software",
        "video_codec_mode": "lossless_ffv1",
        "software_encoder": "ffv1",
        "hardware_encoder": "",
        "container": "mkv",
        "audio_codec": "flac",
        "audio_bitrate": "",
        "description": "FFV1 Lossless + FLAC. True bit-perfect archival. Very heavy — NOT recommended for 4K on this system.",
    },
    "custom": {
        "label": "Custom",
        "description": "Manual configuration. All fields editable.",
    },
}


class ConfigDialog(QDialog):
    saved = Signal(AppSettings)
    # Emitted whenever this dialog verifies or updates encoder states so the
    # controller can replace its stale system_profile with the latest version.
    profile_updated = Signal(object)  # carries the updated SystemProfile

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
        self.subtitle_tab = QWidget()

        self.tabs.addTab(self.device_tab, "Device")
        self.tabs.addTab(self.video_tab, "Video")
        self.tabs.addTab(self.output_tab, "Output")
        self.tabs.addTab(self.subtitle_tab, "Subtitles")

        self._build_device_tab()
        self._build_video_tab()
        self._build_output_tab()
        self._build_subtitle_tab()

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
        
        self.preview_background_mode = QComboBox()
        self.preview_background_mode.addItem("No Preview", "none")
        self.preview_background_mode.addItem("Color", "color")
        self.preview_background_mode.addItem("Camera Preview", "camera")

        self.preview_camera = QComboBox()
        self.audio_device = QComboBox()

        form.addRow("Recording Camera", self.recording_camera)
        form.addRow("Preview Background", self.preview_background_mode)
        form.addRow("Preview Camera", self.preview_camera)
        form.addRow("Microphone", self.audio_device)

        self.recording_camera.currentIndexChanged.connect(self._on_camera_changed)
        self.preview_background_mode.currentIndexChanged.connect(self._on_preview_background_changed)
        self.audio_device.currentIndexChanged.connect(self._on_audio_device_changed)
        self._on_preview_background_changed()

    def _on_preview_background_changed(self) -> None:
        mode = self.preview_background_mode.currentData()
        self.preview_camera.setEnabled(mode == "camera")

    def _build_video_tab(self) -> None:
        main_layout = QVBoxLayout(self.video_tab)

        # --- Output Preset ---
        preset_group = QGroupBox("Output Preset")
        preset_form = QFormLayout(preset_group)
        self.preset_combo = QComboBox()
        for key, info in PRESETS.items():
            self.preset_combo.addItem(info["label"], key)
        self.preset_desc = QLabel()
        self.preset_desc.setWordWrap(True)
        self.preset_desc.setStyleSheet("color: #666; font-size: 11px;")
        preset_form.addRow("Preset", self.preset_combo)
        preset_form.addRow(self.preset_desc)
        main_layout.addWidget(preset_group)

        # --- Capture settings ---
        capture_group = QGroupBox("Capture Source")
        capture_form = QFormLayout(capture_group)
        self.resolution = QComboBox()
        self.fps = QComboBox()
        self.pixel_format = QComboBox()
        capture_form.addRow("Resolution", self.resolution)
        capture_form.addRow("FPS", self.fps)
        capture_form.addRow("Pixel format", self.pixel_format)
        main_layout.addWidget(capture_group)

        # --- Encoding Settings ---
        encoding_group = QGroupBox("Video Encoding")
        self.encoding_form = QFormLayout(encoding_group)

        self.encoder_type = QComboBox()
        self.encoder_type.addItem("Camera Stream Copy (Recommended for 4K)", "copy")
        self.encoder_type.addItem("Software Encoding (CPU)", "software")
        self.encoder_type.addItem("Hardware Encoding (GPU)", "hardware")

        self.software_codec = QComboBox()
        self.software_codec.addItem("H.264 Standard (ultrafast, CRF 23)", "standard")
        self.software_codec.addItem("H.264 High Quality (veryfast, CRF 18)", "high_quality")
        self.software_codec.addItem("H.264 Lossless CPU ⚠ High Risk", "lossless_h264")
        self.software_codec.addItem("FFV1 Lossless CPU ⚠ Archival Only", "lossless_ffv1")
        self.software_codec.addItem("MJPEG Software", "mjpeg")

        self.hardware_codec = QComboBox()

        self.quality_preset = QComboBox()
        self.quality_preset.addItem("High Quality (Balanced)", "hq")
        self.quality_preset.addItem("Visually Lossless", "visually_lossless")
        self.quality_preset.addItem("Maximum Performance (Fast)", "fast")

        self.encoding_form.addRow("Encoding Mode", self.encoder_type)
        self.encoding_form.addRow("Software Codec", self.software_codec)
        self.encoding_form.addRow("Hardware Codec", self.hardware_codec)
        self.encoding_form.addRow("Quality Preset", self.quality_preset)

        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        self.help_label.setStyleSheet("color: #555; font-size: 11px;")
        self.encoding_form.addRow(self.help_label)

        main_layout.addWidget(encoding_group)
        main_layout.addStretch()

        # Signals
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        self.resolution.currentIndexChanged.connect(self._on_resolution_changed)
        self.fps.currentIndexChanged.connect(self._on_fps_changed)
        self.encoder_type.currentIndexChanged.connect(self._on_encoding_mode_changed)
        self._on_encoding_mode_changed()

    def _on_preset_changed(self) -> None:
        key = self.preset_combo.currentData()
        info = PRESETS.get(key, {})
        self.preset_desc.setText(info.get("description", ""))

        if key == "custom":
            # Enable all fields for manual editing
            self.encoder_type.setEnabled(True)
            self.software_codec.setEnabled(True)
            self.hardware_codec.setEnabled(True)
            return

        # Apply preset values to UI fields
        enc_type = info.get("video_encoder_type", "copy")
        with QSignalBlocker(self.encoder_type):
            self._set_combo_by_data(self.encoder_type, enc_type)

        sw_enc = info.get("software_encoder", "")
        codec_mode = info.get("video_codec_mode", "")
        with QSignalBlocker(self.software_codec):
            self._set_combo_by_data(self.software_codec, sw_enc or codec_mode)

        hw_enc = info.get("hardware_encoder", "")
        if hw_enc == "__best__":
            # Resolve best hardware encoder
            best = self.system_profile.best_hardware_encoder()
            hw_enc = best.name if best else ""
        with QSignalBlocker(self.hardware_codec):
            self._set_combo_by_data(self.hardware_codec, hw_enc)

        self._on_encoding_mode_changed()

    def _on_encoding_mode_changed(self) -> None:
        mode = self.encoder_type.currentData()

        self.software_codec.setEnabled(mode == "software")
        self.hardware_codec.setEnabled(mode == "hardware")
        self.quality_preset.setEnabled(mode != "copy")

        if mode == "copy":
            self.help_label.setText(
                "<b>Stream Copy</b>: Passes the camera's MJPEG stream directly to the file "
                "with no re-encoding. Zero CPU overhead. Best for 4K."
            )
        elif mode == "software":
            self.help_label.setText(
                "<b>Software (CPU)</b>: Re-encodes with libx264 or FFV1. "
                "High compatibility but very heavy at 4K — may drop frames on this system."
            )
        elif mode == "hardware":
            if self.hardware_codec.count() == 0 or self.hardware_codec.currentData() is None:
                self.help_label.setText(
                    "<b>Hardware</b>: No hardware encoders detected. "
                    "Use Stream Copy for smooth 4K."
                )
            else:
                enc = self.hardware_codec.currentData() or ""
                self.help_label.setText(
                    f"<b>Hardware</b>: Uses <code>{enc}</code> for GPU-accelerated encoding. "
                    "Lower CPU usage than software. Must be verified usable before recording."
                )

    def _build_output_tab(self) -> None:
        form = QFormLayout(self.output_tab)

        self.container = QComboBox()
        self.output_dir = QLineEdit()
        browse = QPushButton("Browse")
        browse.clicked.connect(self._choose_output_dir)

        self.recording_sample_rate = QComboBox()
        self.recording_channels = QComboBox()
        self.audio_codec = QComboBox()
        self.recording_bit_depth = QComboBox()

        # Will be dynamically populated in _on_audio_device_changed
        self.recording_sample_rate.addItem("48000 Hz", 48000)
        self.recording_channels.addItem("Mono", 1)

        self.audio_codec.addItem("FLAC (lossless)", "flac")
        self.audio_codec.addItem("WAV / PCM (uncompressed)", "wav_pcm")
        self.audio_codec.addItem("MP3", "libmp3lame")
        self.audio_codec.addItem("AAC", "aac")

        self.recording_bit_depth.addItem("16-bit", 16)
        self.recording_bit_depth.addItem("24-bit", 24)
        self.recording_bit_depth.addItem("32-bit", 32)
        
        self.audio_codec.currentIndexChanged.connect(self._on_audio_codec_changed)

        form.addRow("Container", self.container)
        form.addRow("Output directory", self.output_dir)
        form.addRow("", browse)
        form.addRow("Audio Sample Rate", self.recording_sample_rate)
        form.addRow("Audio Channels", self.recording_channels)
        form.addRow("Audio Codec", self.audio_codec)
        form.addRow("Audio Bit Depth", self.recording_bit_depth)

    def _build_subtitle_tab(self) -> None:
        form = QFormLayout(self.subtitle_tab)

        self.subtitle_source = QComboBox()
        self.subtitle_source.addItem("Teleprompter Script (Deterministic)", "script")
        self.subtitle_source.addItem("Voice Recognition (Legacy/Disabled)", "voice")
        self.subtitle_source.setEnabled(False) # For now, voice is disabled as per goal

        self.subtitle_mode = QComboBox()
        self.subtitle_mode.addItem("Phrases (v1)", "phrase")
        self.subtitle_mode.addItem("Word-by-word (v2)", "word")
        self.subtitle_mode.addItem("Both (v1 + v2)", "both")

        self.subtitle_timing_mode = QComboBox()
        self.subtitle_timing_mode.addItem("Manual (Spoken-timed)", SubtitleTimingMode.MANUAL)
        self.subtitle_timing_mode.addItem("Automatic (Timer-driven)", SubtitleTimingMode.AUTO)

        from PySide6.QtWidgets import QSpinBox
        self.words_per_minute = QSpinBox()
        self.words_per_minute.setRange(50, 400)
        self.words_per_minute.setSuffix(" WPM")

        self.subtitle_help = QLabel(
            "<b>Manual</b>: Subtitles match your actual reading speed (driven by keys/scroll).\n"
            "<b>Automatic</b>: Subtitles advance at a fixed speed (driven by timer)."
        )
        self.subtitle_help.setWordWrap(True)
        self.subtitle_help.setStyleSheet("color: #666; font-size: 11px;")

        self.subtitle_timing_mode.currentIndexChanged.connect(self._on_subtitle_timing_changed)

        form.addRow("Source", self.subtitle_source)
        form.addRow("Display Mode", self.subtitle_mode)
        form.addRow("Timing Mode", self.subtitle_timing_mode)
        form.addRow("Reading Speed", self.words_per_minute)
        form.addRow(self.subtitle_help)

    def _on_subtitle_timing_changed(self) -> None:
        is_auto = self.subtitle_timing_mode.currentData() == SubtitleTimingMode.AUTO
        self.words_per_minute.setVisible(is_auto)
        
        # Find the label for this row and hide it too
        layout = self.subtitle_tab.layout()
        if isinstance(layout, QFormLayout):
            label = layout.labelForField(self.words_per_minute)
            if label:
                label.setVisible(is_auto)

    def _on_audio_codec_changed(self) -> None:
        codec = self.audio_codec.currentData()
        # Only WAV/PCM directly maps bit depth via pcm_s16le, etc.
        # FLAC uses 16/24 internally but we can allow it to be visible.
        # MP3/AAC do not use PCM bit depths.
        is_lossless = codec in ("flac", "wav_pcm")
        self.recording_bit_depth.setEnabled(is_lossless)

    def _populate_from_profile(self) -> None:
        self.recording_camera.clear()
        self.preview_camera.clear()
        self.audio_device.clear()
        self.container.clear()

        # Lazy verify any remaining UNSUPPORTED encoders before showing them
        unsupported = [e for e in self.system_profile.video_encoders if e.kind == "hardware" and e.state == EncoderState.UNSUPPORTED]
        if unsupported:
            from teleprompter_app.recording.encoder_probe import verify_encoder_usable
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import Qt
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                for enc in unsupported:
                    usable, reason = verify_encoder_usable("ffmpeg", enc.name)
                    if usable:
                        self.system_profile = self.system_profile.with_encoder_verification(enc.name, EncoderState.AVAILABLE, "")
                    else:
                        self.system_profile = self.system_profile.with_encoder_verification(enc.name, EncoderState.UNAVAILABLE, reason)
                self.system_profile.save_encoder_cache()
            finally:
                QApplication.restoreOverrideCursor()

        # Preview camera: None + Same as Recording + actual cameras
        self.preview_camera.addItem("None (No Preview)", "__none__")
        self.preview_camera.addItem("Same as Recording Camera", "__same_as_recording__")
        for cam in self.system_profile.cameras:
            self.recording_camera.addItem(cam.name, cam.ffmpeg_name)
            self.preview_camera.addItem(cam.name, cam.ffmpeg_name)

        for mic in self.system_profile.audio_inputs:
            self.audio_device.addItem(mic.name, mic.ffmpeg_name)

        if self.audio_device.count() > 0:
            self._on_audio_device_changed()

        # Hardware encoders — run lazy verification for any UNSUPPORTED entries
        unsupported = [
            e for e in self.system_profile.video_encoders
            if e.kind == "hardware" and e.state == EncoderState.UNSUPPORTED
        ]
        if unsupported:
            from teleprompter_app.recording.encoder_probe import verify_encoder_usable
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import Qt
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                for enc in unsupported:
                    usable, reason = verify_encoder_usable("ffmpeg", enc.name)
                    if usable:
                        self.system_profile = self.system_profile.with_encoder_verification(
                            enc.name, EncoderState.AVAILABLE, ""
                        )
                    else:
                        self.system_profile = self.system_profile.with_encoder_verification(
                            enc.name, EncoderState.UNAVAILABLE, reason
                        )
                self.system_profile.save_encoder_cache()
            finally:
                QApplication.restoreOverrideCursor()

        # Propagate the verified profile back to the controller immediately
        self.profile_updated.emit(self.system_profile)

        # Populate hardware encoder combo:
        #   AVAILABLE   → selectable
        #   UNAVAILABLE → greyed out with tooltip (shows failure reason)
        #   UNSUPPORTED → hidden (never shown)
        self.hardware_codec.clear()
        hw_encoders = [
            e for e in self.system_profile.hardware_encoders()
            if e.state != EncoderState.UNSUPPORTED
        ]
        if not hw_encoders:
            self.hardware_codec.addItem("No verified hardware encoders available", None)
            self.hardware_codec.setEnabled(False)
        else:
            for enc in hw_encoders:
                self.hardware_codec.addItem(enc.display_label, enc.name)
                if enc.state == EncoderState.UNAVAILABLE:
                    idx = self.hardware_codec.count() - 1
                    item_model = self.hardware_codec.model()
                    if hasattr(item_model, "item"):
                        item = item_model.item(idx)
                        if item:
                            item.setEnabled(False)
                            item.setToolTip(enc.failure_reason)

        for muxer in self.system_profile.containers:
            self.container.addItem(muxer, muxer)

        self._on_camera_changed()
        # Set initial preset description
        self._on_preset_changed()

    def _selected_camera(self) -> CameraProfile | None:
        ffmpeg_name = self.recording_camera.currentData()
        if not ffmpeg_name:
            return None
        return self.system_profile.camera_by_ffmpeg_name(ffmpeg_name)

    def _on_camera_changed(self) -> None:
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
                    {"format_name": fmt_name, "format_kind": fmt_kind},
                )

    def _on_audio_device_changed(self) -> None:
        mic_name = self.audio_device.currentData()
        if not mic_name:
            return

        mic = next((a for a in self.system_profile.audio_inputs if a.ffmpeg_name == mic_name), None)
        if not mic or not getattr(mic, 'formats', None):
            return

        with QSignalBlocker(self.recording_sample_rate), QSignalBlocker(self.recording_channels):
            prev_sr = self.recording_sample_rate.currentData()
            prev_ch = self.recording_channels.currentData()

            self.recording_sample_rate.clear()
            self.recording_channels.clear()

            rates = sorted(list(set(f[2] for f in mic.formats)), reverse=True)
            channels = sorted(list(set(f[0] for f in mic.formats)), reverse=True)

            if not rates:
                rates = [48000, 44100]
            if not channels:
                channels = [2, 1]

            for sr in rates:
                label = f"{sr} Hz"
                if sr == 48000 or sr == 44100:
                    label += " (Native)"
                elif sr > 48000:
                    label += " (High-Res)"
                self.recording_sample_rate.addItem(label, sr)

            for ch in channels:
                self.recording_channels.addItem("Stereo" if ch == 2 else "Mono", ch)

            if prev_sr:
                self._set_combo_by_data(self.recording_sample_rate, prev_sr)
            if prev_ch:
                self._set_combo_by_data(self.recording_channels, prev_ch)

    def _restore_settings(self) -> None:
        rec_cam = self.settings.recording_video_device or self.settings.video_device
        pre_cam = self.settings.preview_video_device or "__same_as_recording__"

        self._set_combo_by_data(self.recording_camera, rec_cam)
        self._set_combo_by_data(self.preview_camera, pre_cam)
        self._set_combo_by_data(self.preview_background_mode, getattr(self.settings, "preview_background_mode", "color"))
        self._on_preview_background_changed()
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
        codec_key = self.settings.software_encoder or self.settings.video_codec_mode or "standard"
        self._set_combo_by_data(self.software_codec, codec_key)
        self._set_combo_by_data(self.hardware_codec, self.settings.hardware_encoder)
        self._set_combo_by_data(self.quality_preset, self.settings.quality_preset)
        self._on_encoding_mode_changed()

        self._set_combo_by_data(self.audio_device, self.settings.audio_device)
        self._on_audio_device_changed()
        self._set_combo_by_data(self.container, self.settings.container)
        self._set_combo_by_data(self.recording_sample_rate, self.settings.recording_sample_rate)
        self._set_combo_by_data(self.recording_channels, self.settings.recording_channels)
        self._set_combo_by_data(self.audio_codec, self.settings.audio_codec)
        self._set_combo_by_data(self.recording_bit_depth, self.settings.recording_bit_depth)
        self._on_audio_codec_changed()

        # Subtitles
        self._set_combo_by_data(self.subtitle_source, self.settings.subtitle_source)
        self._set_combo_by_data(self.subtitle_mode, self.settings.subtitle_mode)
        self._set_combo_by_data(self.subtitle_timing_mode, self.settings.subtitle_timing_mode)
        self.words_per_minute.setValue(self.settings.words_per_minute)
        self._on_subtitle_timing_changed()

        self.output_dir.setText(self.settings.output_dir)
        # Preset defaults to custom since we're restoring existing settings
        self._set_combo_by_data(self.preset_combo, "custom")

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

        enc_type = self.encoder_type.currentData() or "copy"
        hw_enc = self.hardware_codec.currentData() or ""
        sw_enc = self.software_codec.currentData() or "standard"

        # Correctness guard: hardware mode requires a real encoder name
        if enc_type == "hardware":
            if not hw_enc or hw_enc.lower() == "none":
                QMessageBox.warning(
                    self,
                    "No Hardware Encoder",
                    "Hardware encoding is selected but no hardware encoder is available.\n\n"
                    "Please switch to 'Camera Stream Copy' or 'Software Encoding'.",
                )
                return
            
            enc_prof = self.system_profile.encoder_by_name(hw_enc)
            if not enc_prof or enc_prof.state == EncoderState.UNSUPPORTED:
                QMessageBox.warning(self, "Invalid Encoder", f"The encoder {hw_enc} is unsupported.")
                return

            if enc_prof.state != EncoderState.AVAILABLE:
                from teleprompter_app.recording.encoder_probe import verify_encoder_usable
                from PySide6.QtWidgets import QApplication
                from PySide6.QtCore import Qt
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    usable, reason = verify_encoder_usable("ffmpeg", hw_enc)
                    if not usable:
                        self.system_profile = self.system_profile.with_encoder_verification(hw_enc, EncoderState.UNAVAILABLE, reason)
                        self.system_profile.save_encoder_cache()
                        # Propagate failure so controller profile is also updated
                        self.profile_updated.emit(self.system_profile)
                        QMessageBox.warning(self, "Encoder Verification Failed", f"The encoder {hw_enc} failed to verify:\n{reason}")
                        return
                    self.system_profile = self.system_profile.with_encoder_verification(hw_enc, EncoderState.AVAILABLE, "")
                    self.system_profile.save_encoder_cache()
                    # Propagate success so controller marks this encoder AVAILABLE
                    self.profile_updated.emit(self.system_profile)
                finally:
                    QApplication.restoreOverrideCursor()

        # Derive video_codec_mode from encoder type
        if enc_type == "copy":
            video_codec_mode = "copy"
        elif enc_type == "hardware":
            video_codec_mode = hw_enc
        else:
            video_codec_mode = sw_enc

        updates = {
            "recording_video_device": self.recording_camera.currentData() or "",
            "preview_video_device": self.preview_camera.currentData() or "__same_as_recording__",
            "preview_background_mode": self.preview_background_mode.currentData() or "color",
            "use_camera_background": (self.preview_background_mode.currentData() == "camera"),
            "video_device": self.recording_camera.currentData() or "",  # compat
            "audio_device": self.audio_device.currentData() or "",
            "resolution": self.resolution.currentData() or "",
            "fps": fps_val,
            "pixel_format": fmt_name,
            "input_format_kind": fmt_kind,

            # Encoding
            "video_encoder_type": enc_type,
            "software_encoder": sw_enc if enc_type == "software" else "",
            "hardware_encoder": hw_enc if enc_type == "hardware" else "",
            "video_codec_mode": video_codec_mode,
            "quality_preset": self.quality_preset.currentData() or "hq",

            # Output
            "audio_device": self.audio_device.currentData() or "",
            "container": self.container.currentData() or "mkv",
            "recording_sample_rate": self.recording_sample_rate.currentData() or 48000,
            "recording_channels": self.recording_channels.currentData() or 1,
            "audio_codec": self.audio_codec.currentData() or "flac",
            "recording_bit_depth": self.recording_bit_depth.currentData() or 16,
            "output_dir": self.output_dir.text().strip(),

            # Subtitles
            "subtitle_source": self.subtitle_source.currentData() or "script",
            "subtitle_mode": self.subtitle_mode.currentData() or "both",
            "subtitle_timing_mode": self.subtitle_timing_mode.currentData() or SubtitleTimingMode.MANUAL,
            "words_per_minute": self.words_per_minute.value(),
        }

        settings = current.updated(updates)
        ConfigManager().save(settings)
        self.saved.emit(settings)
        self.accept()
