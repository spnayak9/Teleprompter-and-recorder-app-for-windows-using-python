"""Application controller connecting UI, parsing, audio, and recognition layers."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from teleprompter_app.audio.mic_manager import MicrophoneManager
from teleprompter_app.core.alignment import AlignmentEngine, AlignmentMatch
from teleprompter_app.core.parser import InputType, ScriptParser
from teleprompter_app.core.state_manager import StateManager
from teleprompter_app.core.tokenizer import ScriptTokenizer
from teleprompter_app.recording.audio_config import RecordingConfig
from teleprompter_app.recording.file_manager import RecordingFileManager, RecordingFiles
from teleprompter_app.recording.subtitle_generator import SubtitleGenerator
from teleprompter_app.speech.recognizer import RecognitionResult
from teleprompter_app.speech.vosk_engine import VoskSpeechRecognizer
from teleprompter_app.ui.main_window import MainWindow
from teleprompter_app.utils.config import AppSettings, ConfigManager
from teleprompter_app.ffmpeg_probe import probe_system, SystemProbe
from teleprompter_app.preview import PreviewWorker
from teleprompter_app.camera_mapper import detect_cameras

logger = logging.getLogger(__name__)


class RecognitionBridge(QObject):
    """Thread-safe bridge between the speech worker thread and Qt UI."""

    result_received = Signal(object)
    status_changed = Signal(str)
    error_occurred = Signal(str)


class TeleprompterController(QObject):
    """Top-level application coordinator.

    The controller owns the independent layers and wires them through signals.
    UI classes do not import recognizers or parsers, and core modules do not
    depend on Qt.
    """

    def __init__(self, qt_app: QApplication) -> None:
        super().__init__()
        self.qt_app = qt_app
        self.config = ConfigManager()
        self.settings = self.config.load()

        self.parser = ScriptParser()
        self.tokenizer = ScriptTokenizer()
        self.aligner = AlignmentEngine()
        self.state = StateManager()
        self.microphones = MicrophoneManager()
        self.recording_files = RecordingFileManager()
        self.ffmpeg_recorder = None
        self.current_recording_files: RecordingFiles | None = None
        self.subtitle_generator: SubtitleGenerator | None = None
        self.recording_started_at: float | None = None
        
        # Phase 1: System Profile Single Source of Truth
        logger.info("Probing system capabilities (this may take a few seconds)...")
        self.system_profile = probe_system()
        # opencv_cameras is already inside system_profile.cameras
        self.opencv_cameras = self.system_profile.cameras

        self.window = MainWindow(self.settings, self.system_profile)
        self.recognition_bridge = RecognitionBridge()
        self.recognizer: VoskSpeechRecognizer | None = None
        self.recognizer_started_at: float | None = None
        self.recording_timer = QTimer(self)
        self.recording_timer.setInterval(250)
        self.previewer = None
        self.ffmpeg_recorder = None

        self._connect_signals()
        self.refresh_microphones()
        self.load_default_template()

    def show(self) -> None:
        self.window.show()

    def _connect_signals(self) -> None:
        self.window.script_file_selected.connect(self.load_script_file)
        self.window.start_requested.connect(self.start_recognition)
        self.window.stop_requested.connect(self.stop_recognition)
        self.window.rewind_requested.connect(self.rewind_script)
        self.window.settings_changed.connect(self.apply_settings)
        self.window.microphones_refresh_requested.connect(self.refresh_microphones)
        self.window.start_recording_requested.connect(self.start_recording)
        self.window.stop_recording_requested.connect(self.stop_recording)
        self.window.select_recording_dir_requested.connect(self.select_recording_directory)
        self.window.config_saved.connect(lambda: self._on_recorder_config_saved())
        
        self.window.recording_mode_changed.connect(self._on_recording_mode_changed)
        self.window.background_mode_changed.connect(self._on_background_mode_changed)
        self.window.preview_resolution_changed.connect(self._on_preview_resolution_changed)

        self.recognition_bridge.result_received.connect(self.handle_recognition_result)
        self.recognition_bridge.status_changed.connect(self.window.set_status)
        self.recognition_bridge.error_occurred.connect(self.handle_recognition_error)
        self.recording_timer.timeout.connect(self.update_recording_duration)

        self.qt_app.aboutToQuit.connect(self.shutdown)

    def load_default_template(self) -> None:
        template = Path(__file__).resolve().parent / "templates" / "plain_template.txt"
        if template.exists():
            self.load_script_file(str(template), InputType.PLAIN.value)

    def load_script_file(self, file_path: str, input_mode: str | None = None) -> None:
        path = Path(file_path)
        if not path.exists():
            self.window.set_status(f"File not found: {path}")
            return

        try:
            input_type = InputType(input_mode) if input_mode else None
            parsed = self.parser.parse_file(path, input_type)
            tokenized = self.tokenizer.tokenize_html(parsed.html)
        except Exception as exc:  # UI boundary: show concise user-facing error.
            logger.exception("Could not load script")
            QMessageBox.critical(self.window, "Could not load script", str(exc))
            return

        self.aligner.set_tokens(tokenized.tokens)
        self.state.reset()
        self.window.set_document(tokenized.html, tokenized.tokens)
        self.window.set_status(f"Loaded {path.name}: {len(tokenized.tokens)} words")

    def rewind_script(self) -> None:
        self.aligner.reset()
        self.state.reset()
        self.window.highlight_word(-1)
        self.window.set_status("Script rewound")

    def apply_settings(self, updates: dict) -> None:
        self.settings = self.settings.updated(updates)
        self.config.save(self.settings)
        self.window.apply_settings(self.settings)
        
        # sync main controls if setting changed programmatically
        try:
            if "use_camera_background" in updates:
                idx = self.window.main_controls.background_selector.findData("camera" if updates["use_camera_background"] else "color")
                if idx >= 0:
                    self.window.main_controls.background_selector.setCurrentIndex(idx)
        except Exception:
            pass

        # Start/stop preview based on settings and recorder config
        try:
            from teleprompter_app.config_manager import ConfigManager as RecorderConfigManager

            recorder_mgr = RecorderConfigManager()
            rsettings = recorder_mgr.load()
            # map preview_resolution to width/height
            mapping = {
                "240p": (426, 240),
                "360p": (640, 360),
                "480p": (854, 480),
                "720p": (1280, 720),
            }
            if getattr(self.settings, "use_camera_background", False) and rsettings.video_device:
                size = mapping.get(getattr(self.settings, "preview_resolution", "360p"), (640, 360))
                
                device_idx = -1
                # Try to map the recorder's video_device (name) to an OpenCV index
                if self.system_profile and self.system_profile.cameras:
                    for cam in self.system_profile.cameras:
                        if cam.name == rsettings.video_device:
                            device_idx = cam.opencv_index
                            break
                
                if device_idx < 0 and self.system_profile.cameras:
                    # fallback to first available camera if none matched
                    device_idx = self.system_profile.cameras[0].opencv_index

                if device_idx >= 0:
                    # stop existing previewer if settings changed
                    if self.previewer is not None:
                        try:
                            self.previewer.stop()
                            self.previewer.deleteLater()
                        except Exception:
                            pass
                    
                    self.previewer = PreviewWorker(device_idx, width=size[0], height=size[1])
                    self.previewer.frame_ready.connect(self.window.preview_overlay.set_frame)
                    self.previewer.fps_ready.connect(self.window.preview_overlay.set_fps)
                    
                    try:
                        self.previewer.start()
                        self.window.preview_overlay.enable_preview(True)
                    except Exception:
                        logger.exception("Could not start previewer")
                else:
                    logger.warning("No valid camera index found for preview")
            else:
                if self.previewer is not None:
                    try:
                        self.previewer.stop()
                        self.previewer.deleteLater()
                    except Exception:
                        pass
                    self.previewer = None
                try:
                    self.window.preview_overlay.enable_preview(False)
                except Exception:
                    pass
            # apply background color to preview overlay (fallback when not using camera)
            try:
                self.window.preview_overlay.set_background_color(getattr(self.settings, "background_color", "#000000"))
            except Exception:
                pass
        except Exception:
            logger.exception("Preview integration failed")

        if self.recognizer and self.recognizer.is_running:
            self.window.set_status("Settings saved. Restart listening to change microphone or model.")

    def refresh_microphones(self) -> None:
        devices = self.microphones.list_input_devices()
        self.window.set_microphones(devices, self.settings.microphone_index)
        if not devices:
            self.window.set_status("No microphone devices found. Check the audio backend installation.")

    def start_recognition(self) -> None:
        if not self.aligner.has_tokens:
            self.window.set_status("Load a script before starting recognition.")
            return

        if self.recognizer and self.recognizer.is_running:
            self.window.set_status("Recognition is already running.")
            return

        model_path = Path(self.settings.vosk_model_path).expanduser()
        self.recognizer = VoskSpeechRecognizer(
            model_path=model_path,
            device_index=self.settings.microphone_index,
            sample_rate=self.settings.sample_rate,
            block_size=self.settings.audio_block_size,
            grammar=self._build_script_grammar(),
        )
        self.recognizer_started_at = time.monotonic()
        self.recognizer.start(
            on_result=self.recognition_bridge.result_received.emit,
            on_status=self.recognition_bridge.status_changed.emit,
            on_error=self.recognition_bridge.error_occurred.emit,
        )
        self.state.set_listening(True)
        self.window.set_listening(True)

    def stop_recognition(self) -> None:
        if self.recognizer:
            self.recognizer.stop()
            self.recognizer = None
        self.recognizer_started_at = None
        self.state.set_listening(False)
        self.window.set_listening(False)
        self.window.set_status("Recognition stopped")

    def _on_recorder_config_saved(self) -> None:
        # Config dialog saved recorder settings.
        # Per architectural correction: Do NOT restart preview or probe on save to avoid UI freeze.
        # Settings will be used when recording starts or when background is toggled.
        self.window.set_status("Recording settings saved.")

    def handle_recognition_result(self, result: RecognitionResult) -> None:
        # Convert recognizer word timings to recording-relative times and
        # align words one-by-one so we can attach token timestamps to the
        # subtitle generator (script-based SRT) instead of raw recognizer text.
        matches: list = []

        recognizer_audio_started_at = (
            self.recognizer.audio_started_at
            if self.recognizer and self.recognizer.audio_started_at is not None
            else self.recognizer_started_at
        )
        if recognizer_audio_started_at is None:
            recognition_offset = 0.0
        else:
            recognition_offset = (
                recognizer_audio_started_at - self.recording_started_at
                if self.recording_started_at is not None
                else 0.0
            )
        fallback_elapsed = time.monotonic() - self.recording_started_at if self.recording_started_at is not None else 0.0

        if result.words:
            for word in result.words:
                match = self.aligner.align_word(word.word, word.confidence)
                if match is not None:
                    matches.append(match)
                    # Attach script token timestamp to subtitle generator when recording
                    if self.subtitle_generator is not None and self.recording_started_at is not None:
                        start = None if word.start is None else max(0.0, word.start + recognition_offset)
                        end = None if word.end is None else max(0.0, word.end + recognition_offset)
                        if start is None:
                            start = max(0.0, fallback_elapsed)
                        if end is None or end <= start:
                            end = max(start + 0.05, fallback_elapsed)
                        try:
                            self.subtitle_generator.add_token_match(match.token_index, match.token_word, start, end)
                        except Exception:
                            logger.exception("Failed to add token match to subtitle generator")
        else:
            # No word-level timings — fall back to previous behavior
            self.capture_subtitles(result)

        self._apply_matches(matches, result)

    def _apply_matches(self, matches: Iterable[AlignmentMatch], result: RecognitionResult) -> None:
        latest: AlignmentMatch | None = None
        for match in matches:
            latest = match

        if latest is None:
            if result.text:
                self.state.set_last_spoken_text(result.text)
            return

        self.state.update_word(latest.token_index, latest.confidence, result.text)
        self.window.highlight_word(latest.token_index, latest.confidence)

    def handle_recognition_error(self, message: str) -> None:
        self.stop_recognition()
        QMessageBox.warning(self.window, "Speech recognition unavailable", message)

    def select_recording_directory(self) -> None:
        directory = self.window.choose_project_folder(self.settings.recording_project_dir)
        if not directory:
            return
        self.apply_settings({"recording_project_dir": directory})
        self.window.set_recording_directory(directory)

    def start_recording(self) -> None:
        if self.ffmpeg_recorder and getattr(self.ffmpeg_recorder, "is_running", False):
            self.window.set_recording_status("Already recording")
            return

        directory = self.window.choose_project_folder(self.settings.recording_project_dir)
        if not directory:
            self.window.set_recording_status("Recording cancelled")
            return

        self.apply_settings({"recording_project_dir": directory})
        self.window.set_recording_directory(directory)

        # Determine requested recording mode from toolbar controls
        mode_text = None
        try:
            mode_text = self.window.main_controls.mode.currentText()
        except Exception:
            # fallback to persisted recorder config
            try:
                from teleprompter_app.config_manager import ConfigManager as RecorderConfigManager

                rec_mgr = RecorderConfigManager()
                rsettings = rec_mgr.load()
                mode_text = getattr(rsettings, "recording_mode", None)
            except Exception:
                mode_text = None

        # Load recorder settings (preferred source for recording-specific options)
        try:
            from teleprompter_app.config_manager import ConfigManager as RecorderConfigManager

            rec_mgr = RecorderConfigManager()
            rsettings = rec_mgr.load()
            # If no explicit mode selected in UI, use persisted setting
            if not mode_text:
                mode_text = getattr(rsettings, "recording_mode", None)
        except Exception:
            rsettings = None

        # Phase 10: Validation Engine
        # Prevent recording if the active configuration violates system capabilities
        if rsettings and mode_text and ("video" in mode_text.lower() or "main view" in mode_text.lower()):
            if not getattr(rsettings, "video_device", None):
                QMessageBox.warning(self.window, "Validation Error", "No camera selected in configuration. Please Configure Video.")
                return
            
            valid_camera = False
            if self.system_profile and getattr(self.system_profile, "cameras", None):
                for cam in self.system_profile.cameras:
                    if cam.name == rsettings.video_device:
                        valid_camera = True
                        break
            if not valid_camera:
                QMessageBox.warning(self.window, "Validation Error", f"Selected camera '{rsettings.video_device}' is not currently plugged in or available.")
                return

        if rsettings and mode_text and "audio" in mode_text.lower():
            if not getattr(rsettings, "audio_device", None):
                QMessageBox.warning(self.window, "Validation Error", "No microphone selected in configuration. Please Configure Audio.")
                return

        # Map mode to booleans: (audio, video, srt)
        mode_map = {
            "record main view": (False, True, False),
            "record only srt": (False, False, True),
            "record only audio": (True, False, False),
            "record only video": (False, True, False),
            "audio with srt": (True, False, True),
            "video with srt": (False, True, True),
            "audio and video only": (True, True, False),
            "audio + video + srt": (True, True, True),
        }

        audio_enabled, video_enabled, srt_enabled = mode_map.get(mode_text or "", (True, False, True))

        # Prepare an audio RecordingConfig for file naming and audio settings
        # Prefer recorder settings when available
        audio_config = RecordingConfig(
            sample_rate=(rsettings.sample_rate if rsettings is not None else self.settings.recording_sample_rate),
            bit_depth=(getattr(rsettings, "bit_depth", None) or self.settings.recording_bit_depth),
            channels=(rsettings.channels if rsettings is not None else self.settings.recording_channels),
            output_format=(getattr(rsettings, "container", None) or self.settings.recording_format),
        )

        try:
            files = self.recording_files.prepare_session(Path(directory), audio_config)
        except Exception as exc:
            logger.exception("Could not prepare recording session")
            QMessageBox.critical(self.window, "Could not prepare recording", str(exc))
            return

        # STOP preview first (critical to avoid hardware conflict)
        if self.previewer is not None:
            try:
                self.previewer.stop()
                self.previewer = None
                self.window.preview_overlay.enable_preview(False)
            except Exception:
                pass

        # Decide which recorder(s) to start
        try:
            # Use FFmpegRecorder for ALL recording (audio, video, or both)
            from teleprompter_app.recorder import FFmpegRecorder, RecorderConfig as FFRecConfig
            from teleprompter_app.config_manager import ConfigManager as RecorderConfigManager

            rec_mgr = RecorderConfigManager()
            rsettings = rec_mgr.load()

            if video_enabled or audio_enabled:
                # Construct FFmpeg configuration based on user options
                ext = rsettings.container if video_enabled else "mka" # Matroska audio default for audio-only
                if audio_enabled and not video_enabled:
                    ext = "flac" if rsettings.audio_codec in ["flac", ""] else rsettings.container
                
                out_file = files.audio_dir / f"{files.session_name}.{ext}"
                
                ff_cfg = FFRecConfig(
                    ffmpeg_path="ffmpeg",
                    video_device=rsettings.video_device if video_enabled else None,
                    audio_device=rsettings.audio_device if audio_enabled else None,
                    width=int(rsettings.resolution.split("x")[0]) if video_enabled and rsettings.resolution and "x" in rsettings.resolution else None,
                    height=int(rsettings.resolution.split("x")[1]) if video_enabled and rsettings.resolution and "x" in rsettings.resolution else None,
                    fps=rsettings.fps if video_enabled else None,
                    pixel_format=rsettings.pixel_format if video_enabled else None,
                    video_codec=rsettings.video_codec if video_enabled else "",
                    audio_codec=rsettings.audio_codec if audio_enabled else "",
                    audio_sample_rate=rsettings.sample_rate if audio_enabled else None,
                    audio_channels=rsettings.channels if audio_enabled else None,
                    output_container=ext,
                    rtbufsize=rsettings.rtbufsize,
                    thread_queue_size=rsettings.thread_queue_size,
                    extra_ffmpeg_args=(rsettings.extra_ffmpeg_args.split() if rsettings.extra_ffmpeg_args else None),
                )

                # special-case: capture the application main window (rendered teleprompter)
                if mode_text == "record main view" and video_enabled:
                    try:
                        title = str(self.window.windowTitle()) or "desktop"
                        ff_cfg.video_device = f"screen:{title}"
                    except Exception:
                        ff_cfg.video_device = "screen:desktop"

                self.ffmpeg_recorder = FFmpegRecorder(ff_cfg, out_file)
                self.ffmpeg_recorder.start()

            else:
                # srt-only: no recorder
                self.ffmpeg_recorder = None

        except Exception as exc:
            logger.exception("Could not start recording")
            self.window.set_recording(False, "Recording failed")
            QMessageBox.critical(self.window, "Could not start recording", str(exc))
            return

        self.current_recording_files = files
        # mark recording start time
        self.recording_started_at = time.monotonic()

        self.recording_timer.start()
        self.window.set_recording(True, "Recording 00:00")
        self.window.set_status(f"Recording session {files.session_name} in {files.project_root}")

        if not (self.recognizer and self.recognizer.is_running):
            self.start_recognition()

    def stop_recording(self) -> None:
        # Stop any running ffmpeg recorders
        try:
            if self.ffmpeg_recorder is not None:
                try:
                    self.ffmpeg_recorder.stop()
                except Exception:
                    logger.exception("Error stopping ffmpeg recorder")
                finally:
                    self.ffmpeg_recorder = None
        finally:
            self.recording_timer.stop()

        if self.subtitle_generator is not None:
            self.subtitle_generator.finish()
            self.subtitle_generator = None

        self.window.set_recording(False, "Saved")
        self.recording_started_at = None

        if self.current_recording_files is not None:
            status = f"Recording saved in {self.current_recording_files.project_root}"
            self.window.set_status(status)
            self.current_recording_files = None

    def update_recording_duration(self) -> None:
        if self.recording_started_at is None:
            return
        elapsed = int(time.monotonic() - self.recording_started_at)
        minutes, seconds = divmod(elapsed, 60)
        self.window.set_recording_status(f"Recording {minutes:02}:{seconds:02}")

    def capture_subtitles(self, result: RecognitionResult) -> None:
        if self.subtitle_generator is None or self.recording_started_at is None:
            return

        recognizer_audio_started_at = (
            self.recognizer.audio_started_at
            if self.recognizer and self.recognizer.audio_started_at is not None
            else self.recognizer_started_at
        )
        if recognizer_audio_started_at is None:
            recognition_offset = 0.0
        else:
            recognition_offset = recognizer_audio_started_at - self.recording_started_at
        fallback_elapsed = time.monotonic() - self.recording_started_at
        self.subtitle_generator.add_result(result, recognition_offset, fallback_elapsed)

    def _build_script_grammar(self) -> list[str]:
        words: list[str] = []
        seen: set[str] = set()
        for token in self.aligner.tokens:
            if not token.normalized or token.normalized in seen:
                continue
            seen.add(token.normalized)
            words.append(token.normalized)
            if len(words) >= 2500:
                break

        if words:
            words.append("[unk]")
        return words

    def shutdown(self) -> None:
        self.stop_recording()
        self.stop_recognition()
        self.config.save(self.settings)

    def _on_recording_mode_changed(self, mode: str) -> None:
        try:
            from teleprompter_app.config_manager import ConfigManager as RecorderConfigManager
            rec_mgr = RecorderConfigManager()
            rsettings = rec_mgr.load()
            rsettings.recording_mode = mode
            rec_mgr.save(rsettings)
        except Exception:
            pass

    def _on_background_mode_changed(self, bg_type: str) -> None:
        use_camera = (bg_type == "camera")
        self.apply_settings({"use_camera_background": use_camera})

    def _on_preview_resolution_changed(self, res: str) -> None:
        self.apply_settings({"preview_resolution": res})
