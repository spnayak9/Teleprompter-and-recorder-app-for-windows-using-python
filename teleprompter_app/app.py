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
from teleprompter_app.recording.audio_recorder import LosslessAudioRecorder
from teleprompter_app.recording.file_manager import RecordingFileManager, RecordingFiles
from teleprompter_app.recording.subtitle_generator import SubtitleGenerator
from teleprompter_app.speech.recognizer import RecognitionResult
from teleprompter_app.speech.vosk_engine import VoskSpeechRecognizer
from teleprompter_app.ui.main_window import MainWindow
from teleprompter_app.utils.config import AppSettings, ConfigManager

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
        self.recorder: LosslessAudioRecorder | None = None
        self.current_recording_files: RecordingFiles | None = None
        self.subtitle_generator: SubtitleGenerator | None = None
        self.recording_started_at: float | None = None

        self.window = MainWindow(self.settings)
        self.recognition_bridge = RecognitionBridge()
        self.recognizer: VoskSpeechRecognizer | None = None
        self.recognizer_started_at: float | None = None
        self.recording_timer = QTimer(self)
        self.recording_timer.setInterval(250)
        self.previewer = None
        self.audio_recorder = None
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

        # Start/stop preview based on settings and recorder config
        try:
            from teleprompter_app.config_manager import ConfigManager as RecorderConfigManager
            from teleprompter_app.preview import PreviewConfig, Previewer

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
                cfg = PreviewConfig(camera_name=rsettings.video_device, preview_size=size, desired_fps=rsettings.fps)
                # stop existing previewer if settings changed
                if self.previewer is not None:
                    try:
                        self.previewer.stop()
                    except Exception:
                        pass
                self.previewer = Previewer(cfg, frame_callback=lambda f: self.window.preview_overlay.set_frame(f), fps_callback=lambda v: self.window.preview_overlay.set_fps(v))
                try:
                    self.previewer.start()
                except Exception:
                    logger.exception("Could not start previewer")
                # update recording controls mode display
                try:
                    self.window.recording_controls.set_selected_mode(f"{rsettings.resolution} @{rsettings.fps} {rsettings.video_codec}")
                except Exception:
                    pass
            else:
                if self.previewer is not None:
                    try:
                        self.previewer.stop()
                    except Exception:
                        pass
                    self.previewer = None
        except Exception:
            logger.debug("Preview integration not available or failed to start")

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
        if (self.audio_recorder and getattr(self.audio_recorder, "is_running", False)) or (
            self.ffmpeg_recorder and getattr(self.ffmpeg_recorder, "is_running", False)
        ):
            self.window.set_recording_status("Already recording")
            return

        directory = self.window.choose_project_folder(self.settings.recording_project_dir)
        if not directory:
            self.window.set_recording_status("Recording cancelled")
            return

        self.apply_settings({"recording_project_dir": directory})
        self.window.set_recording_directory(directory)

        # Determine requested recording mode from toolbar controls (if present)
        mode_text = None
        try:
            mode_text = self.window.recording_controls.mode.currentText()
        except Exception:
            mode_text = None

        # Map mode to booleans: (audio, video, srt)
        mode_map = {
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
        audio_config = RecordingConfig(
            sample_rate=self.settings.recording_sample_rate,
            bit_depth=self.settings.recording_bit_depth,
            channels=self.settings.recording_channels,
            output_format=self.settings.recording_format,
        )

        try:
            files = self.recording_files.prepare_session(Path(directory), audio_config)
        except Exception as exc:
            logger.exception("Could not prepare recording session")
            QMessageBox.critical(self.window, "Could not prepare recording", str(exc))
            return

        # Create subtitle generator if requested (or always create for convenience)
        self.subtitle_generator = SubtitleGenerator(files.srt_path, files.transcript_path) if srt_enabled or True else None

        # Decide which recorder(s) to start
        try:
            # Use FFmpegRecorder for video (and combined audio+video)
            from teleprompter_app.recorder import FFmpegRecorder, RecorderConfig as FFRecConfig
            from teleprompter_app.config_manager import ConfigManager as RecorderConfigManager

            rec_mgr = RecorderConfigManager()
            rsettings = rec_mgr.load()

            # If both audio and video requested, prefer a single ffmpeg process capturing both
            if video_enabled:
                out_file = files.audio_dir / f"{files.session_name}.{rsettings.container}"
                ff_cfg = FFRecConfig(
                    ffmpeg_path="ffmpeg",
                    video_device=rsettings.video_device or None,
                    audio_device=rsettings.audio_device or None,
                    width=int(rsettings.resolution.split("x")[0]) if "x" in rsettings.resolution else None,
                    height=int(rsettings.resolution.split("x")[1]) if "x" in rsettings.resolution else None,
                    fps=rsettings.fps,
                    pixel_format=None,
                    video_codec=rsettings.video_codec,
                    audio_codec=rsettings.audio_codec,
                    audio_sample_rate=rsettings.sample_rate,
                    audio_channels=rsettings.channels,
                    output_container=rsettings.container,
                    rtbufsize=rsettings.rtbufsize,
                    thread_queue_size=rsettings.thread_queue_size,
                    extra_ffmpeg_args=(rsettings.extra_ffmpeg_args.split() if rsettings.extra_ffmpeg_args else None),
                )

                self.ffmpeg_recorder = FFmpegRecorder(ff_cfg, out_file)
                self.ffmpeg_recorder.start()

                # If user also wants lossless separate audio, we skip starting LosslessAudioRecorder
                self.audio_recorder = None

            elif audio_enabled:
                # audio-only path uses existing LosslessAudioRecorder (lossless PCM/FLAC)
                recorder = LosslessAudioRecorder()
                recorder.start(device_index=self.settings.microphone_index, config=audio_config, files=files)
                self.audio_recorder = recorder
                self.ffmpeg_recorder = None
            else:
                # srt-only: no recorder
                self.audio_recorder = None
                self.ffmpeg_recorder = None

        except Exception as exc:
            logger.exception("Could not start recording")
            self.window.set_recording(False, "Recording failed")
            QMessageBox.critical(self.window, "Could not start recording", str(exc))
            return

        self.current_recording_files = files
        # mark recording start time: prefer audio recorder's started time
        if self.audio_recorder is not None and getattr(self.audio_recorder, "started_at", None):
            self.recording_started_at = self.audio_recorder.started_at
        else:
            self.recording_started_at = time.monotonic()

        self.recording_timer.start()
        self.window.set_recording(True, "Recording 00:00")
        self.window.set_status(f"Recording session {files.session_name} in {files.project_root}")

        if not (self.recognizer and self.recognizer.is_running):
            self.start_recognition()

    def stop_recording(self) -> None:
        # Stop any running audio or ffmpeg recorders
        audio_result = None
        try:
            if self.audio_recorder is not None:
                try:
                    audio_result = self.audio_recorder.stop()
                except Exception:
                    logger.exception("Error stopping audio recorder")
                finally:
                    self.audio_recorder = None

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
            if audio_result is not None and getattr(audio_result, "dropped_chunks", 0):
                status += f" ({audio_result.dropped_chunks} audio buffers dropped)"
            if audio_result is not None and getattr(audio_result, "wav_flac_match", True) is False:
                status += " (WAV/FLAC verification failed)"
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
