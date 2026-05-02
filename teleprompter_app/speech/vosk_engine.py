"""Vosk-based offline streaming speech recognition."""

from __future__ import annotations

import audioop
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
from teleprompter_app.core.tokenizer import normalize_word

logger = logging.getLogger(__name__)


class Pcm16Resampler:
    """Small stateful PCM16 mono resampler for device rates that are not 16 kHz."""

    def __init__(self, source_rate: int, target_rate: int) -> None:
        self.source_rate = source_rate
        self.target_rate = target_rate
        self._state = None

    @property
    def is_needed(self) -> bool:
        return self.source_rate != self.target_rate

    def convert(self, chunk: bytes) -> bytes:
        if not self.is_needed:
            return chunk
        converted, self._state = audioop.ratecv(
            chunk,
            2,
            1,
            self.source_rate,
            self.target_rate,
            self._state,
        )
        return converted


class VoskSpeechRecognizer(SpeechRecognizer):
    """Offline-first streaming recognizer using Vosk and microphone audio."""

    def __init__(
        self,
        model_path: Path,
        device_index: int | None,
        sample_rate: int = 16000,
        block_size: int = 4000,
        grammar: list[str] | None = None,
    ) -> None:
        self.model_path = model_path
        self.device_index = device_index
        self.sample_rate = sample_rate
        min_block = max(320, sample_rate // 50)
        max_block = max(min_block, sample_rate // 20)
        self.block_size = max(min_block, min(block_size, max_block))
        self.grammar = grammar or []
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
        emitted_partial_words: list[str] = []

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

            self._stream = AudioStream(
                device_index=self.device_index,
                sample_rate=self.sample_rate,
                block_size=self.block_size,
            )
            opened_device = self._stream.start()
            resampler = Pcm16Resampler(opened_device.sample_rate, self.sample_rate)

            if on_status:
                fallback_note = f" {self._stream.last_status}" if self._stream.last_status else ""
                resample_note = (
                    f" Resampling {opened_device.sample_rate} Hz to {self.sample_rate} Hz for Vosk."
                    if resampler.is_needed
                    else ""
                )
                on_status(
                    f"Microphone ready: {opened_device.name} ({opened_device.host_api}), "
                    f"{opened_device.sample_rate} Hz.{resample_note}{fallback_note}"
                )

            if on_status:
                on_status(self._model_status_message())

            model = Model(str(self.model_path))
            recognizer = self._create_recognizer(KaldiRecognizer, model)
            recognizer.SetWords(True)
            if hasattr(recognizer, "SetPartialWords"):
                recognizer.SetPartialWords(True)

            if on_status:
                mode = "script grammar" if self._uses_runtime_grammar() else "full model"
                chunk_label = (
                    "PortAudio-selected buffer"
                    if opened_device.block_size == 0
                    else f"{opened_device.block_size} sample chunks"
                )
                on_status(f"Listening with {chunk_label} ({mode})...")

            for chunk in self._stream.chunks(self._stop_event):
                chunk = resampler.convert(chunk)
                if not chunk:
                    continue
                if recognizer.AcceptWaveform(chunk):
                    payload = json.loads(recognizer.Result())
                    result = self._payload_to_result(payload, is_final=True)
                    new_words = self._new_words_after_partial(result.words, emitted_partial_words)
                    emitted_word_count = 0
                    emitted_partial_words = []
                    if new_words:
                        on_result(RecognitionResult(" ".join(w.word for w in new_words), new_words, True))
                else:
                    payload = json.loads(recognizer.PartialResult())
                    result = self._payload_to_result(payload, is_final=False)
                    if result.words:
                        current_partial_words = [normalize_word(word.word) for word in result.words]
                        if self._starts_with(current_partial_words, emitted_partial_words):
                            emitted_word_count = min(emitted_word_count, len(result.words))
                            new_words = result.words[emitted_word_count:]
                        else:
                            new_words = result.words
                        emitted_word_count = len(result.words)
                        emitted_partial_words = current_partial_words
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

    def _create_recognizer(self, recognizer_class, model):  # noqa: ANN001
        if not self._uses_runtime_grammar():
            return recognizer_class(model, self.sample_rate)

        try:
            return recognizer_class(model, self.sample_rate, json.dumps(self.grammar))
        except Exception:
            logger.exception("Could not create Vosk grammar recognizer; falling back to full model")
            return recognizer_class(model, self.sample_rate)

    def _model_status_message(self) -> str:
        size_mb = self._model_size_mb()
        model_name = self.model_path.name
        grammar_message = "Using script grammar to reduce latency." if self._uses_runtime_grammar() else "Using full model graph."
        if "lgraph" in model_name.lower() or size_mb >= 150:
            return (
                f"Loading large Vosk model ({model_name}, {size_mb:.0f} MB). "
                f"{grammar_message}"
            )
        return f"Loading Vosk model ({model_name}, {size_mb:.0f} MB). {grammar_message}"

    def _model_size_mb(self) -> float:
        try:
            size = sum(path.stat().st_size for path in self.model_path.rglob("*") if path.is_file())
        except OSError:
            return 0.0
        return size / (1024 * 1024)

    def _new_words_after_partial(
        self,
        final_words: list[RecognizedWord],
        emitted_partial_words: list[str],
    ) -> list[RecognizedWord]:
        final_normalized = [normalize_word(word.word) for word in final_words]
        if self._starts_with(final_normalized, emitted_partial_words):
            return final_words[len(emitted_partial_words) :]
        return final_words

    def _starts_with(self, words: list[str], prefix: list[str]) -> bool:
        if len(prefix) > len(words):
            return False
        return words[: len(prefix)] == prefix

    def _uses_runtime_grammar(self) -> bool:
        return bool(self.grammar and self._model_supports_runtime_grammar())

    def _model_supports_runtime_grammar(self) -> bool:
        graph_dir = self.model_path / "graph"
        return (graph_dir / "HCLr.fst").exists() and (graph_dir / "Gr.fst").exists()
