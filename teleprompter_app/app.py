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
        self.capture_subtitles(result)
        matches = self.aligner.align_words(result.words)
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
        if self.recorder and self.recorder.is_running:
            self.window.set_recording_status("Already recording")
            return

        directory = self.window.choose_project_folder(self.settings.recording_project_dir)
        if not directory:
            self.window.set_recording_status("Recording cancelled")
            return

        self.apply_settings({"recording_project_dir": directory})
        self.window.set_recording_directory(directory)

        config = RecordingConfig(
            sample_rate=self.settings.recording_sample_rate,
            bit_depth=self.settings.recording_bit_depth,
            channels=self.settings.recording_channels,
            output_format=self.settings.recording_format,
        )

        try:
            files = self.recording_files.prepare_session(Path(directory), config)
            recorder = LosslessAudioRecorder()
            recorder.start(
                device_index=self.settings.microphone_index,
                config=config,
                files=files,
            )
        except Exception as exc:
            logger.exception("Could not start recording")
            self.window.set_recording(False, "Recording failed")
            QMessageBox.critical(self.window, "Could not start recording", str(exc))
            return

        self.recorder = recorder
        self.current_recording_files = files
        self.subtitle_generator = SubtitleGenerator(files.srt_path, files.transcript_path)
        self.recording_started_at = recorder.started_at or time.monotonic()
        self.recording_timer.start()
        self.window.set_recording(True, "Recording 00:00")
        self.window.set_status(f"Recording raw audio to {files.audio_dir}")

        if not (self.recognizer and self.recognizer.is_running):
            self.start_recognition()

    def stop_recording(self) -> None:
        if not self.recorder:
            self.window.set_recording(False, "Idle")
            return

        try:
            result = self.recorder.stop()
        except Exception as exc:
            logger.exception("Could not stop recording cleanly")
            QMessageBox.warning(self.window, "Recording error", str(exc))
            result = None
        finally:
            self.recorder = None
            self.recording_timer.stop()

        if self.subtitle_generator is not None:
            self.subtitle_generator.finish()
            self.subtitle_generator = None

        self.window.set_recording(False, "Saved")
        self.recording_started_at = None

        if self.current_recording_files is not None:
            status = f"Recording saved in {self.current_recording_files.project_root}"
            if result is not None and result.dropped_chunks:
                status += f" ({result.dropped_chunks} audio buffers dropped)"
            if result is not None and result.wav_flac_match is False:
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
