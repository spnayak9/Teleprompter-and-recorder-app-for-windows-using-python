"""Vosk-based offline streaming speech recognition."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Event, Thread

from teleprompter_app.audio.audio_stream import AudioStream
from teleprompter_app.speech.recognizer import (
    ErrorCallback,
    RecognizedWord,
    RecognitionResult,
    ResultCallback,
    SpeechRecognizer,
    StatusCallback,
)

logger = logging.getLogger(__name__)


class VoskSpeechRecognizer(SpeechRecognizer):
    """Offline-first streaming recognizer using Vosk and microphone audio."""

    def __init__(
        self,
        model_path: Path,
        device_index: int | None,
        sample_rate: int = 16000,
        block_size: int = 4000,
    ) -> None:
        self.model_path = model_path
        self.device_index = device_index
        self.sample_rate = sample_rate
        self.block_size = block_size
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._stream: AudioStream | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(
        self,
        on_result: ResultCallback,
        on_status: StatusCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        if self._running:
            return

        self._stop_event.clear()
        self._running = True
        self._thread = Thread(
            target=self._run,
            args=(on_result, on_status, on_error),
            name="VoskRecognitionThread",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._stream is not None:
            self._stream.stop()
            self._stream = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None
        self._running = False

    def _run(
        self,
        on_result: ResultCallback,
        on_status: StatusCallback | None,
        on_error: ErrorCallback | None,
    ) -> None:
        emitted_word_count = 0

        try:
            if not self.model_path.exists():
                raise RuntimeError(
                    "Vosk model not found. Download a Vosk model, unzip it, and set "
                    f"the model path in Settings. Current path: {self.model_path}"
                )

            try:
                from vosk import KaldiRecognizer, Model
            except ImportError as exc:
                raise RuntimeError("Speech recognition requires the 'vosk' package.") from exc

            if on_status:
                on_status("Loading Vosk model...")

            model = Model(str(self.model_path))
            recognizer = KaldiRecognizer(model, self.sample_rate)
            recognizer.SetWords(True)
            if hasattr(recognizer, "SetPartialWords"):
                recognizer.SetPartialWords(True)

            self._stream = AudioStream(
                device_index=self.device_index,
                sample_rate=self.sample_rate,
                block_size=self.block_size,
            )
            self._stream.start()

            if on_status:
                on_status("Listening...")

            for chunk in self._stream.chunks(self._stop_event):
                if recognizer.AcceptWaveform(chunk):
                    payload = json.loads(recognizer.Result())
                    result = self._payload_to_result(payload, is_final=True)
                    emitted_word_count = min(emitted_word_count, len(result.words))
                    new_words = result.words[emitted_word_count:]
                    emitted_word_count = 0
                    if new_words:
                        on_result(RecognitionResult(" ".join(w.word for w in new_words), new_words, True))
                else:
                    payload = json.loads(recognizer.PartialResult())
                    result = self._payload_to_result(payload, is_final=False)
                    if result.words:
                        emitted_word_count = min(emitted_word_count, len(result.words))
                        new_words = result.words[emitted_word_count:]
                        emitted_word_count = len(result.words)
                        if new_words:
                            on_result(RecognitionResult(" ".join(w.word for w in new_words), new_words, False))

        except Exception as exc:
            logger.exception("Vosk recognition failed")
            if on_error:
                on_error(str(exc))
        finally:
            if self._stream is not None:
                self._stream.stop()
                self._stream = None
            self._running = False
            if on_status and not self._stop_event.is_set():
                on_status("Recognition stopped")

    def _payload_to_result(self, payload: dict, is_final: bool) -> RecognitionResult:
        text = payload.get("text") or payload.get("partial") or ""
        raw_words = payload.get("result") or payload.get("partial_result") or []

        if raw_words:
            words = [
                RecognizedWord(
                    word=str(item.get("word", "")),
                    start=item.get("start"),
                    end=item.get("end"),
                    confidence=item.get("conf"),
                    is_final=is_final,
                )
                for item in raw_words
                if item.get("word")
            ]
        else:
            words = [RecognizedWord(word=word, is_final=is_final) for word in text.split()]

        return RecognitionResult(text=text, words=words, is_final=is_final)
