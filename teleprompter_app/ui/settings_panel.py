"""Settings panel for rendering, audio, and recognition options."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from teleprompter_app.audio.mic_manager import MicrophoneDevice
from teleprompter_app.core.parser import InputType
from teleprompter_app.recording.audio_config import RecordingFormat
from teleprompter_app.recording.audio_config import SUPPORTED_SAMPLE_RATES, BitDepth, ChannelMode
from teleprompter_app.utils.config import AppSettings


class SettingsPanel(QWidget):
    """Control panel that emits normalized setting dictionaries."""

    settings_changed = Signal(dict)
    refresh_microphones_requested = Signal()
    start_requested = Signal()
    stop_requested = Signal()
    start_recording_requested = Signal()
    stop_recording_requested = Signal()
    select_recording_dir_requested = Signal()

    def __init__(self, settings: AppSettings, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.settings = settings
        self._devices: list[MicrophoneDevice] = []
        self._building = False
        self._build_ui()
        self.apply_settings(settings)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        text_group = QGroupBox("Text")
        text_form = QFormLayout(text_group)

        self.font_family = QFontComboBox()
        self.font_size = QSpinBox()
        self.font_size.setRange(18, 120)
        self.font_size.setSingleStep(2)

        self.text_color_button = QPushButton()
        self.text_color_button.setToolTip("Choose text color")

        self.bold = QCheckBox("Bold")
        self.italic = QCheckBox("Italic")
        self.underline = QCheckBox("Underline")

        style_row = QHBoxLayout()
        style_row.addWidget(self.bold)
        style_row.addWidget(self.italic)
        style_row.addWidget(self.underline)

        text_form.addRow("Font", self.font_family)
        text_form.addRow("Size", self.font_size)
        text_form.addRow("Color", self.text_color_button)
        text_form.addRow("Style", style_row)

        render_group = QGroupBox("Highlighting")
        render_form = QFormLayout(render_group)
        self.highlight_color_button = QPushButton()
        self.highlight_color_button.setToolTip("Choose highlight color")
        self.scroll_speed = QSlider(Qt.Orientation.Horizontal)
        self.scroll_speed.setRange(1, 100)
        self.scroll_speed.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.scroll_speed.setTickInterval(10)
        render_form.addRow("Highlight", self.highlight_color_button)
        render_form.addRow("Scroll speed", self.scroll_speed)

        input_group = QGroupBox("Input")
        input_form = QFormLayout(input_group)
        self.input_mode = QComboBox()
        self.input_mode.addItem("Auto from file", "")
        self.input_mode.addItem("Plain text", InputType.PLAIN.value)
        self.input_mode.addItem("Markdown", InputType.MARKDOWN.value)
        self.input_mode.addItem("HTML", InputType.HTML.value)
        input_form.addRow("Mode", self.input_mode)

        audio_group = QGroupBox("Audio")
        audio_form = QFormLayout(audio_group)
        self.microphone = QComboBox()
        self.refresh_mics = QPushButton("Refresh")
        mic_row = QHBoxLayout()
        mic_row.addWidget(self.microphone, 1)
        mic_row.addWidget(self.refresh_mics)

        self.model_path = QLineEdit()
        self.model_path.setPlaceholderText("Path to Vosk model directory")
        self.browse_model = QPushButton("Browse")
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_path, 1)
        model_row.addWidget(self.browse_model)

        audio_form.addRow("Microphone", mic_row)
        audio_form.addRow("Vosk model", model_row)

        recording_group = QGroupBox("Recording")
        recording_form = QFormLayout(recording_group)

        background_group = QGroupBox("Background")
        background_form = QFormLayout(background_group)
        self.use_camera_background = QCheckBox("Use camera as background")
        self.preview_resolution = QComboBox()
        self.preview_resolution.addItem("240p", "240p")
        self.preview_resolution.addItem("360p", "360p")
        self.preview_resolution.addItem("480p", "480p")
        self.preview_resolution.addItem("720p", "720p")

        self.recording_dir = QLineEdit()
        self.recording_dir.setReadOnly(True)
        self.recording_dir.setPlaceholderText("Choose project folder before recording")
        self.select_recording_dir = QPushButton("Select")
        recording_dir_row = QHBoxLayout()
        recording_dir_row.addWidget(self.recording_dir, 1)
        recording_dir_row.addWidget(self.select_recording_dir)

        self.recording_format = QComboBox()
        self.recording_format.addItem("Both WAV + FLAC", RecordingFormat.BOTH.value)
        self.recording_format.addItem("WAV only", RecordingFormat.WAV.value)
        self.recording_format.addItem("FLAC only", RecordingFormat.FLAC.value)

        self.recording_sample_rate = QComboBox()
        self.recording_sample_rate.addItem("48000 Hz", 48000)
        self.recording_sample_rate.addItem("44100 Hz", 44100)

        self.recording_bit_depth = QComboBox()
        self.recording_bit_depth.addItem("16-bit PCM", 16)
        self.recording_bit_depth.addItem("24-bit PCM", 24)

        self.recording_channels = QComboBox()
        self.recording_channels.addItem("Mono", 1)
        self.recording_channels.addItem("Stereo", 2)

        recording_controls = QHBoxLayout()
        self.start_recording_button = QPushButton("Start Recording")
        self.stop_recording_button = QPushButton("Stop Recording")
        self.stop_recording_button.setEnabled(False)
        recording_controls.addWidget(self.start_recording_button)
        recording_controls.addWidget(self.stop_recording_button)

        self.recording_status = QLabel("Idle")
        self.recording_status.setMinimumWidth(160)

        recording_form.addRow("Project", recording_dir_row)
        recording_form.addRow("Format", self.recording_format)
        recording_form.addRow("Sample rate", self.recording_sample_rate)
        recording_form.addRow("Bit depth", self.recording_bit_depth)
        recording_form.addRow("Channels", self.recording_channels)
        recording_form.addRow("Status", self.recording_status)
        recording_form.addRow("Controls", recording_controls)

        controls = QGroupBox("Session")
        controls_layout = QHBoxLayout(controls)
        self.start_button = QPushButton("Start Listening")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        controls_layout.addWidget(self.start_button)
        controls_layout.addWidget(self.stop_button)

        root.addWidget(text_group)
        root.addWidget(render_group)
        root.addWidget(background_group)
        root.addWidget(input_group)
        root.addWidget(audio_group)
        root.addWidget(recording_group)
        root.addWidget(controls)
        root.addStretch(1)

        self.font_family.currentFontChanged.connect(lambda _font: self._emit_settings())
        self.font_size.valueChanged.connect(lambda _value: self._emit_settings())
        self.text_color_button.clicked.connect(lambda _checked=False: self._choose_color("text_color"))
        self.highlight_color_button.clicked.connect(lambda _checked=False: self._choose_color("highlight_color"))
        self.bold.toggled.connect(lambda _checked: self._emit_settings())
        self.italic.toggled.connect(lambda _checked: self._emit_settings())
        self.underline.toggled.connect(lambda _checked: self._emit_settings())
        self.scroll_speed.valueChanged.connect(lambda _value: self._emit_settings())
        self.input_mode.currentIndexChanged.connect(lambda _index: self._emit_settings())
        self.microphone.currentIndexChanged.connect(lambda _index: self._emit_settings())
        self.use_camera_background.toggled.connect(lambda _checked: self._emit_settings())
        self.preview_resolution.currentIndexChanged.connect(lambda _index: self._emit_settings())
        self.microphone.currentIndexChanged.connect(lambda _index: self._on_microphone_selection_changed())
        self.model_path.editingFinished.connect(self._emit_settings)
        self.recording_format.currentIndexChanged.connect(lambda _index: self._emit_settings())
        self.recording_sample_rate.currentIndexChanged.connect(lambda _index: self._emit_settings())
        self.recording_bit_depth.currentIndexChanged.connect(lambda _index: self._emit_settings())
        self.recording_channels.currentIndexChanged.connect(lambda _index: self._emit_settings())
        self.browse_model.clicked.connect(lambda _checked=False: self._browse_model())
        self.refresh_mics.clicked.connect(lambda _checked=False: self.refresh_microphones_requested.emit())
        self.start_button.clicked.connect(lambda _checked=False: self.start_requested.emit())
        self.stop_button.clicked.connect(lambda _checked=False: self.stop_requested.emit())
        self.select_recording_dir.clicked.connect(lambda _checked=False: self.select_recording_dir_requested.emit())
        self.start_recording_button.clicked.connect(lambda _checked=False: self.start_recording_requested.emit())
        self.stop_recording_button.clicked.connect(lambda _checked=False: self.stop_recording_requested.emit())

    def apply_settings(self, settings: AppSettings) -> None:
        self._building = True
        self.settings = settings
        self.font_family.setCurrentFont(QFont(settings.font_family))
        self.font_size.setValue(settings.font_size)
        self.bold.setChecked(settings.bold)
        self.italic.setChecked(settings.italic)
        self.underline.setChecked(settings.underline)
        self.scroll_speed.setValue(settings.scroll_speed)
        self.model_path.setText(settings.vosk_model_path)
        self.recording_dir.setText(settings.recording_project_dir)
        self._set_combo_by_data(self.recording_format, settings.recording_format)
        self._set_combo_by_data(self.recording_sample_rate, settings.recording_sample_rate)
        self._set_combo_by_data(self.recording_bit_depth, settings.recording_bit_depth)
        self._set_combo_by_data(self.recording_channels, settings.recording_channels)
        self._set_button_color(self.text_color_button, settings.text_color, "Text color")
        self._set_button_color(self.highlight_color_button, settings.highlight_color, "Highlight color")

        self.use_camera_background.setChecked(getattr(settings, "use_camera_background", False))
        self._set_combo_by_data(self.preview_resolution, getattr(settings, "preview_resolution", "360p"))

        index = self.input_mode.findData(settings.input_mode)
        self.input_mode.setCurrentIndex(index if index >= 0 else 0)
        self._building = False

    def set_microphones(self, devices: list[MicrophoneDevice], selected_index: int | None) -> None:
        self._building = True
        self._devices = devices
        self.microphone.clear()
        if not devices:
            self.microphone.addItem("No microphones found", -1)
            self.microphone.setEnabled(False)
        else:
            self.microphone.setEnabled(True)
            for device in devices:
                self.microphone.addItem(device.label, device.index)
            selected = self.microphone.findData(selected_index)
            self.microphone.setCurrentIndex(selected if selected >= 0 else 0)
            # Update recording option controls based on the selected device
            cur_index = self.microphone.currentIndex()
            if cur_index >= 0:
                device_index = self.microphone.currentData()
                for dev in devices:
                    if dev.index == device_index:
                        self._update_recording_options_for_device(dev)
                        break
        self._building = False

    def _on_microphone_selection_changed(self) -> None:
        if self._building:
            return
        idx = self.microphone.currentIndex()
        if idx < 0:
            return
        device_index = self.microphone.currentData()
        for dev in getattr(self, "_devices", []):
            if dev.index == device_index:
                self._update_recording_options_for_device(dev)
                break

    def _update_recording_options_for_device(self, device: MicrophoneDevice) -> None:
        """Probe a device and populate sample rate / bit depth / channel controls.

        This attempts to use sounddevice.check_input_settings to validate common
        combinations. If sounddevice is unavailable or probing fails, fall back
        to reasonable defaults derived from the device's reported properties.
        """
        # Build candidate options
        sample_rates = list(SUPPORTED_SAMPLE_RATES)
        bit_depths = [int(BitDepth.PCM_16), int(BitDepth.PCM_24)]
        channels = [1]
        if device.max_input_channels >= 2:
            channels.append(2)

        # Try probing with sounddevice where possible (non-blocking checks)
        try:
            import sounddevice as sd

            available_rates: list[int] = []
            for rate in sample_rates:
                try:
                    sd.check_input_settings(device=device.index, samplerate=rate, channels=1)
                    available_rates.append(rate)
                except Exception:
                    # try stereo if mono failed but device supports stereo
                    if device.max_input_channels >= 2:
                        try:
                            sd.check_input_settings(device=device.index, samplerate=rate, channels=2)
                            available_rates.append(rate)
                        except Exception:
                            pass

            if available_rates:
                sample_rates = sorted(set(available_rates), reverse=True)

            # Bit depth probing
            available_bits: list[int] = []
            for bit in bit_depths:
                dtype = "int24" if bit == 24 else "int16"
                try:
                    sd.check_input_settings(device=device.index, samplerate=sample_rates[0], channels=1, dtype=dtype)
                    available_bits.append(bit)
                except Exception:
                    # try stereo if mono fails
                    if device.max_input_channels >= 2:
                        try:
                            sd.check_input_settings(device=device.index, samplerate=sample_rates[0], channels=2, dtype=dtype)
                            available_bits.append(bit)
                        except Exception:
                            pass

            if available_bits:
                bit_depths = available_bits
        except Exception:
            # If probing fails, fall back to device defaults
            sample_rates = [device.default_sample_rate] + [r for r in sample_rates if r != device.default_sample_rate]

        # Populate combos
        self.recording_sample_rate.clear()
        for rate in sample_rates:
            self.recording_sample_rate.addItem(f"{rate} Hz", rate)

        self.recording_bit_depth.clear()
        for bit in bit_depths:
            self.recording_bit_depth.addItem(f"{bit}-bit PCM", bit)

        self.recording_channels.clear()
        for ch in channels:
            label = "Mono" if ch == 1 else "Stereo"
            self.recording_channels.addItem(label, ch)

    def set_listening(self, listening: bool) -> None:
        self.start_button.setEnabled(not listening)
        self.stop_button.setEnabled(listening)

    def set_recording(self, recording: bool, status: str = "") -> None:
        self.start_recording_button.setEnabled(not recording)
        self.stop_recording_button.setEnabled(recording)
        if status:
            self.recording_status.setText(status)

    def set_recording_status(self, status: str) -> None:
        self.recording_status.setText(status)

    def set_recording_directory(self, directory: str) -> None:
        self.recording_dir.setText(directory)
        self._emit_settings()

    def current_input_mode(self) -> str:
        return str(self.input_mode.currentData() or "")

    def _emit_settings(self) -> None:
        if self._building:
            return
        microphone_index = self.microphone.currentData()
        self.settings_changed.emit(
            {
                "font_family": self.font_family.currentFont().family(),
                "font_size": self.font_size.value(),
                "text_color": self.settings.text_color,
                "bold": self.bold.isChecked(),
                "italic": self.italic.isChecked(),
                "underline": self.underline.isChecked(),
                "highlight_color": self.settings.highlight_color,
                "scroll_speed": self.scroll_speed.value(),
                "input_mode": self.current_input_mode(),
                "microphone_index": int(microphone_index) if microphone_index is not None else -1,
                "vosk_model_path": self.model_path.text().strip(),
                "recording_project_dir": self.recording_dir.text().strip(),
                "recording_format": str(self.recording_format.currentData()),
                "recording_sample_rate": int(self.recording_sample_rate.currentData()),
                "recording_bit_depth": int(self.recording_bit_depth.currentData()),
                "recording_channels": int(self.recording_channels.currentData()),
                "use_camera_background": bool(self.use_camera_background.isChecked()),
                "preview_resolution": str(self.preview_resolution.currentData()),
            }
        )

    def _choose_color(self, field_name: str) -> None:
        current = QColor(getattr(self.settings, field_name))
        color = QColorDialog.getColor(current, self, "Choose color")
        if not color.isValid():
            return

        value = color.name()
        self.settings = self.settings.updated({field_name: value})
        button = self.text_color_button if field_name == "text_color" else self.highlight_color_button
        label = "Text color" if field_name == "text_color" else "Highlight color"
        self._set_button_color(button, value, label)
        self._emit_settings()

    def _browse_model(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Vosk model directory",
            str(Path(self.model_path.text()).expanduser()) if self.model_path.text() else str(Path.home()),
        )
        if directory:
            self.model_path.setText(directory)
            self._emit_settings()

    def _set_button_color(self, button: QPushButton, color: str, label: str) -> None:
        button.setText(color)
        button.setStyleSheet(
            f"QPushButton {{ background: {color}; color: {self._contrast_color(color)}; padding: 6px; }}"
        )
        button.setAccessibleName(label)

    def _set_combo_by_data(self, combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _contrast_color(self, color: str) -> str:
        qcolor = QColor(color)
        if not qcolor.isValid():
            return "#ffffff"
        luminance = 0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue()
        return "#111111" if luminance > 150 else "#ffffff"
