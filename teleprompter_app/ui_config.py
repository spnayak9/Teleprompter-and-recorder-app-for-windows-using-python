"""Configuration dialog with tabs for device, video, audio, performance, and output.

Lightweight PySide6 dialog that persists settings using `ConfigManager`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import threading
import re

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
from teleprompter_app.audio.mic_manager import MicrophoneManager
from teleprompter_app.system_profile import load_profile_file, SystemProfile, CameraDevice, CameraMode
from teleprompter_app.ffmpeg_probe import probe_ffmpeg, probe_system, FFmpegCapabilities, SystemProbe


class ConfigDialog(QDialog):
    saved = Signal(object)
    # Emitted when ffmpeg probing completes with FFmpegCapabilities or None
    ffmpeg_probed = Signal(object)
    system_probed = Signal(object)

    def __init__(self, system_profile: SystemProbe, config_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recording Configuration")
        self.manager = ConfigManager(config_path)
        self.settings = self.manager.load()
        # runtime caches
        self._profile: SystemProbe = system_profile
        self._system_probe = system_profile
        self._ffmpeg_caps = system_profile.ffmpeg

        self._build_ui()
        self._populate_initial_data()

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

        # initialize dynamic fields from loaded settings
        pass

    def _build_device_tab(self) -> None:
        form = QFormLayout(self._device_tab)
        # device selectors are populated dynamically
        self.video_device = QComboBox()
        self.audio_device = QComboBox()
        form.addRow("Camera device", self.video_device)
        form.addRow("Microphone device", self.audio_device)

        # Device options populate natively into the configuration


        # populate device lists from passed profile
        try:
            self.video_device.addItem("", "")
            if self._profile:
                for cam in self._profile.cameras:
                    self.video_device.addItem(cam.name, cam.name)
        except Exception:
            pass

        try:
            mm = MicrophoneManager()
            mics = mm.list_input_devices()
            self.audio_device.addItem("", "")
            self._audio_devices = mics
            for mic in mics:
                self.audio_device.addItem(mic.label, mic.index)
            # hook audio device selection to populate audio tab options
            try:
                self.audio_device.currentIndexChanged.connect(lambda _i: self._on_audio_device_changed())
            except Exception:
                pass
        except Exception:
            pass

    def _choose_dir(self, line: QLineEdit) -> None:
        # placeholder: device selection could be improved to query DirectShow
        path = QFileDialog.getExistingDirectory(self, "Select device (placeholder)")
        if path:
            line.setText(path)

    def _populate_initial_data(self) -> None:
        # Pre-select video device if it exists in settings
        pref = getattr(self.settings, "video_device", None)
        if pref:
            idx = self.video_device.findText(pref)
            if idx >= 0:
                self.video_device.setCurrentIndex(idx)
        # Populate resolutions from system probe if available
        self._on_camera_changed()

    def _build_video_tab(self) -> None:
        form = QFormLayout(self._video_tab)
        self.resolution = QComboBox()
        self.resolution.addItem("", "")
        self.fps = QComboBox()
        self.fps.addItem("", "")
        self.pixel_format = QComboBox()
        self.pixel_format.addItem("", "")
        self.video_codec = QComboBox()
        self.video_codec.addItem("", "")
        
        self.lossless = QCheckBox()
        self.lossless.setChecked(self.settings.lossless)
        
        form.addRow("Resolution", self.resolution)
        form.addRow("FPS", self.fps)
        form.addRow("Pixel format", self.pixel_format)
        form.addRow("Video codec", self.video_codec)
        form.addRow("Lossless", self.lossless)

        # wire exact dependency chain
        try:
            self.video_device.currentIndexChanged.connect(self._on_video_device_changed)
            self.resolution.currentIndexChanged.connect(self._on_resolution_changed)
            self.fps.currentIndexChanged.connect(self._on_fps_changed)
            self.pixel_format.currentIndexChanged.connect(self._on_format_changed)
            self.video_codec.currentIndexChanged.connect(self._validate_selection)
        except Exception:
            pass

    def _build_audio_tab(self) -> None:
        form = QFormLayout(self._audio_tab)
        self.sample_rate = QComboBox()
        # default placeholder rates; will be replaced by dynamic probing when saved
        for r in (48000, 44100, 32000, 24000, 16000):
            self.sample_rate.addItem(f"{r} Hz", r)
        self.channels = QComboBox()
        self.channels.addItem("1", 1)
        self.channels.addItem("2", 2)
        self.audio_codec = QComboBox()
        self.audio_codec.addItem("", "")
        form.addRow("Sample rate", self.sample_rate)
        form.addRow("Channels", self.channels)
        form.addRow("Audio codec", self.audio_codec)

    def _on_audio_device_changed(self) -> None:
        # Populate sample rate and channel options based on selected audio device
        try:
            import sounddevice as sd
        except Exception:
            return

        idx = self.audio_device.currentData()
        if idx is None or not hasattr(self, "_audio_devices"):
            return
        # find MicrophoneDevice with matching index
        dev = None
        for d in getattr(self, "_audio_devices", []):
            if d.index == idx:
                dev = d
                break
        if dev is None:
            return

        candidate_rates = [48000, 44100, 32000, 24000, 16000]
        available = []
        for r in candidate_rates:
            try:
                sd.check_input_settings(device=dev.index, samplerate=r, channels=1)
                available.append(r)
            except Exception:
                if dev.max_input_channels >= 2:
                    try:
                        sd.check_input_settings(device=dev.index, samplerate=r, channels=2)
                        available.append(r)
                    except Exception:
                        pass

        if not available:
            available = [dev.default_sample_rate] if dev.default_sample_rate else candidate_rates

        self.sample_rate.clear()
        for r in sorted(set(available), reverse=True):
            self.sample_rate.addItem(f"{r} Hz", r)

        # channels
        self.channels.clear()
        self.channels.addItem("1", 1)
        if dev.max_input_channels >= 2:
            self.channels.addItem("2", 2)

    def _build_perf_tab(self) -> None:
        form = QFormLayout(self._perf_tab)
        self.rtbuf = QComboBox()
        for s in ("50M", "100M", "200M", "500M", "1G", "2G"):
            self.rtbuf.addItem(s, s)
        self.thread_q = QComboBox()
        for q in ("128", "256", "512", "1024", "2048", "4096"):
            self.thread_q.addItem(q, int(q))
            
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
        # container must be chosen from supported muxers (populated by ffmpeg probe)
        self.container = QComboBox()
        self.container.addItem("", "")
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

    def _start_ffmpeg_probe(self) -> None:
        try:
            caps = probe_ffmpeg()
        except Exception:
            caps = None
        # emit on main thread via signal
        try:
            self.ffmpeg_probed.emit(caps)
        except Exception:
            # fallback: call handler directly
            try:
                self._on_ffmpeg_probed(caps)
            except Exception:
                pass

    def _start_system_probe(self) -> None:
        try:
            sp = probe_system()
        except Exception:
            sp = None
        try:
            self.system_probed.emit(sp)
        except Exception:
            try:
                self._on_system_probed(sp)
            except Exception:
                pass

    def _on_system_probed(self, sp: Optional[SystemProbe]) -> None:
        # populate camera and audio device dropdowns from live probe results
        if not sp:
            return
        self._system_probe = sp
        try:
            # cameras
            if sp.cameras:
                self.video_device.clear()
                self.video_device.addItem("", "")
                for cam in sp.cameras:
                    self.video_device.addItem(cam.name, cam.name)
            # audios
            if sp.audios:
                self.audio_device.clear()
                self.audio_device.addItem("", "")
                for a in sp.audios:
                    self.audio_device.addItem(a.name, a.name)
        except Exception:
            pass

    def _on_ffmpeg_probed(self, caps: Optional[FFmpegCapabilities]) -> None:
        # store and populate codec/muxer/pix selections
        self._ffmpeg_caps = caps
        if not caps:
            return
        try:
            self.video_codec.clear()
            self.video_codec.addItem("", "")
            for v in caps.video_encoders:
                self.video_codec.addItem(v, v)

            self.audio_codec.clear()
            self.audio_codec.addItem("", "")
            for a in caps.audio_encoders:
                self.audio_codec.addItem(a, a)

            self.container.clear()
            self.container.addItem("", "")
            for m in caps.muxers:
                self.container.addItem(m, m)

            self.pixel_format.clear()
            self.pixel_format.addItem("", "")
            for p in caps.pixel_formats:
                self.pixel_format.addItem(p, p)

            # refine rtbuf suggestions based on system RAM if available
            if self._profile and self._profile.ram:
                total = self._profile.ram.total_gb
                self.rtbuf.clear()
                if total < 8:
                    opts = ["50M", "100M", "200M"]
                elif total < 16:
                    opts = ["100M", "200M", "500M"]
                else:
                    opts = ["200M", "500M", "1G"]
                for o in opts:
                    self.rtbuf.addItem(o, o)
        except Exception:
            pass
        self._validate_selection()

    def _on_video_device_changed(self) -> None:
        name = self.video_device.currentData()
        self.resolution.clear()
        self.resolution.addItem("", "")
        self.fps.clear()
        self.pixel_format.clear()
        self.video_codec.clear()
        
        if not name or not self._system_probe or not self._system_probe.cameras:
            return
            
        cam = next((c for c in self._system_probe.cameras if c.name == name), None)
        if not cam:
            return
            
        res_set = {f"{m.width}x{m.height}" for m in cam.modes}
        for r in sorted(res_set, key=lambda x: int(x.split('x')[0]) * int(x.split('x')[1]), reverse=True):
            self.resolution.addItem(r, r)
            
        self._validate_selection()

    def _on_resolution_changed(self) -> None:
        res = self.resolution.currentData()
        name = self.video_device.currentData()
        self.fps.clear()
        self.pixel_format.clear()
        self.video_codec.clear()
        
        if not res or not name or not self._system_probe:
            return
            
        try:
            w, h = map(int, res.split("x"))
        except Exception:
            return
            
        cam = next((c for c in self._system_probe.cameras if c.name == name), None)
        if not cam:
            return
            
        # Get all modes that match this resolution
        matching_modes = [m for m in cam.modes if m.width == w and m.height == h]
        fps_set = set()
        for m in matching_modes:
            if m.fps > 0:
                fps_set.add(m.fps)
                
        self.fps.addItem("", "")
        for f in sorted(fps_set, reverse=True):
            self.fps.addItem(f"{f:.1f}", f)
            
        self._validate_selection()

    def _on_fps_changed(self) -> None:
        fps_val = self.fps.currentData()
        res = self.resolution.currentData()
        name = self.video_device.currentData()
        self.pixel_format.clear()
        self.video_codec.clear()
        
        if not fps_val or not res or not name or not self._system_probe:
            return
            
        try:
            w, h = map(int, res.split("x"))
        except Exception:
            return
            
        cam = next((c for c in self._system_probe.cameras if c.name == name), None)
        if not cam:
            return
            
        # Get matching mode for resolution + fps
        mode = next((m for m in cam.modes if m.width == w and m.height == h and abs(m.fps - float(fps_val)) < 0.1), None)
        if not mode:
            return
            
        formats = set()
        for f in mode.formats:
            formats.add(f)
            
        self.pixel_format.addItem("", "")
        # prioritize mjpeg, then yuyv422
        for p in sorted(formats, key=lambda x: 0 if 'mjpeg' in x.lower() else 1):
            self.pixel_format.addItem(p, p)
            
        self._validate_selection()

    def _on_format_changed(self) -> None:
        fmt = self.pixel_format.currentData()
        self.video_codec.clear()
        
        if not fmt or not self._ffmpeg_caps:
            return
            
        self.video_codec.addItem("", "")
        
        # Determine logical encoders based on pixel format / camera output
        # E.g. if camera outputs mjpeg, allow 'copy' or 'mjpeg'
        if 'mjpeg' in str(fmt).lower():
            self.video_codec.addItem("copy", "copy")
            self.video_codec.addItem("mjpeg", "mjpeg")
        
        # Always allow standard hardware/software transcoders if available
        preferred = ["hevc_nvenc", "h264_nvenc", "h264_qsv", "h264_amf", "libx264"]
        for p in preferred:
            if p in self._ffmpeg_caps.video_encoders:
                self.video_codec.addItem(p, p)
        
        self._validate_selection()

    def _validate_selection(self) -> None:
        """Validate that current selection forms a plausible capture config.

        Disable Save if an obvious invalid combination is chosen.
        """
        errors = []
        cam_name = self.video_device.currentData()
        res = self.resolution.currentData()
        fps = self.fps.currentData()
        fmt = self.pixel_format.currentData()
        vc = self.video_codec.currentData()

        # If camera is selected, ensure it has a valid configuration chain picked
        if cam_name:
            if not res: errors.append("Select a resolution")
            if not fps: errors.append("Select an FPS")
            if not fmt: errors.append("Select a format")
            if not vc: errors.append("Select a video codec")

        if self._ffmpeg_caps:
            ac = self.audio_codec.currentData()
            if ac and ac not in self._ffmpeg_caps.audio_encoders:
                errors.append("Selected audio codec not supported by ffmpeg")
            cont = self.container.currentData() or self.container.currentText()
            if cont and cont not in self._ffmpeg_caps.muxers:
                errors.append("Selected container not supported by ffmpeg")
                
        try:
            for w in self.findChildren(QPushButton):
                if w.text().lower() == "save":
                    w.setEnabled(len(errors) == 0)
                    w.setToolTip("\n".join(errors))
                    break
        except Exception:
            pass

    def _save(self) -> None:
        s = RecorderSettings(
            video_device=str(self.video_device.currentData() or "").strip(),
            audio_device=str(self.audio_device.currentData() or "").strip(),
            resolution=str(self.resolution.currentData() or self.resolution.currentText()).strip(),
            fps=int(float(self.fps.currentData() or 30)),
            pixel_format=str(self.pixel_format.currentData() or "yuv420p"),
            video_codec=str(self.video_codec.currentData() or self.settings.video_codec),
            lossless=bool(self.lossless.isChecked()),
            sample_rate=int(self.sample_rate.currentData() or self.settings.sample_rate),
            channels=int(self.channels.currentData() or self.settings.channels),
            audio_codec=str(self.audio_codec.currentData() or self.settings.audio_codec),
            rtbufsize=str(self.rtbuf.currentData() or self.settings.rtbufsize),
            thread_queue_size=int(self.thread_q.currentData() or self.settings.thread_queue_size),
            hw_accel=bool(self.hw_accel.isChecked()),
            container=(str(self.container.currentText()).strip() if hasattr(self, "container") else self.container.text().strip()) or self.settings.container,
            output_dir=self.output_dir.text().strip() or self.settings.output_dir,
            naming_pattern=self.settings.naming_pattern,
            extra_ffmpeg_args=self.extra_args.text().strip(),
        )

        self.manager.save(s)
        self.saved.emit(s)
        self.accept()


__all__ = ["ConfigDialog"]
