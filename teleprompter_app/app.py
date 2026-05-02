"""Application controller connecting UI, parsing, audio, and recognition layers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from teleprompter_app.audio.mic_manager import MicrophoneManager
from teleprompter_app.core.alignment import AlignmentEngine, AlignmentMatch
from teleprompter_app.core.parser import InputType, ScriptParser
from teleprompter_app.core.state_manager import StateManager
from teleprompter_app.core.tokenizer import ScriptTokenizer
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

        self.window = MainWindow(self.settings)
        self.recognition_bridge = RecognitionBridge()
        self.recognizer: VoskSpeechRecognizer | None = None

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

        self.recognition_bridge.result_received.connect(self.handle_recognition_result)
        self.recognition_bridge.status_changed.connect(self.window.set_status)
        self.recognition_bridge.error_occurred.connect(self.handle_recognition_error)

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
        )
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
        self.state.set_listening(False)
        self.window.set_listening(False)
        self.window.set_status("Recognition stopped")

    def handle_recognition_result(self, result: RecognitionResult) -> None:
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

    def shutdown(self) -> None:
        self.stop_recognition()
        self.config.save(self.settings)
