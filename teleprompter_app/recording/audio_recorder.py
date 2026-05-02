"""Raw lossless microphone recording pipeline."""

from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from dataclasses import dataclass

from teleprompter_app.recording.audio_config import MAX_RECORDING_QUEUE_CHUNKS, RecordingConfig
from teleprompter_app.recording.file_manager import RecordingFiles

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RecordingResult:
    """Summary of a completed recording session."""

    duration_seconds: float
    frames_written: int
    dropped_chunks: int
    wav_verified: bool | None
    wav_flac_match: bool | None


class LosslessAudioRecorder:
    """Capture raw microphone bytes and stream them to WAV/FLAC files.

    The capture path intentionally performs no noise suppression, filtering,
    normalization, AGC, sample-rate conversion, or gain changes. WAV receives
    the raw callback bytes directly. FLAC receives the same PCM samples encoded
    losslessly for storage.
    """

    def __init__(self) -> None:
        self.config: RecordingConfig | None = None
        self.files: RecordingFiles | None = None
        self._stream = None
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=MAX_RECORDING_QUEUE_CHUNKS)
        self._writer_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._writer_error: Exception | None = None
        self._started_at: float | None = None
        self._frames_written = 0
        self._dropped_chunks = 0

    @property
    def is_running(self) -> bool:
        return self._stream is not None

    @property
    def started_at(self) -> float | None:
        return self._started_at

    def start(self, device_index: int | None, config: RecordingConfig, files: RecordingFiles) -> None:
        if self.is_running:
            raise RuntimeError("Recording is already running.")

        config.validate()
        self._ensure_output_dependencies(config)

        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Recording requires the 'sounddevice' package.") from exc

        device = device_index if device_index is not None and device_index >= 0 else None
        self._validate_input_settings(sd, device, config)

        self.config = config
        self.files = files
        self._stop_event.clear()
        self._writer_error = None
        self._frames_written = 0
        self._dropped_chunks = 0
        self._clear_queue()

        self._writer_thread = threading.Thread(target=self._writer_loop, name="LosslessAudioWriter", daemon=True)
        self._writer_thread.start()

        def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if status:
                logger.warning("Recording input status: %s", status)
            try:
                self._queue.put_nowait(bytes(indata))
            except queue.Full:
                self._dropped_chunks += 1

        try:
            self._stream = sd.RawInputStream(
                samplerate=config.sample_rate,
                blocksize=max(256, config.sample_rate // 100),
                device=device,
                dtype=config.sounddevice_dtype,
                channels=config.channels,
                latency="low",
                callback=callback,
            )
            self._stream.start()
        except Exception:
            self._queue.put(None)
            if self._writer_thread and self._writer_thread.is_alive():
                self._writer_thread.join(timeout=5.0)
            self._writer_thread = None
            self._stream = None
            raise

        self._started_at = time.monotonic()

    def stop(self) -> RecordingResult:
        if not self.is_running:
            return RecordingResult(0.0, 0, self._dropped_chunks, None, None)

        started_at = self._started_at or time.monotonic()
        self._stop_event.set()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        self._queue.put(None)
        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=10.0)
        self._writer_thread = None

        if self._writer_error is not None:
            raise RuntimeError(f"Recording file write failed: {self._writer_error}") from self._writer_error

        wav_verified, wav_flac_match = self._verify_outputs()
        return RecordingResult(
            duration_seconds=max(0.0, time.monotonic() - started_at),
            frames_written=self._frames_written,
            dropped_chunks=self._dropped_chunks,
            wav_verified=wav_verified,
            wav_flac_match=wav_flac_match,
        )

    def _writer_loop(self) -> None:
        assert self.config is not None
        assert self.files is not None

        wav_file = None
        flac_file = None
        try:
            if self.files.wav_path is not None:
                wav_file = wave.open(str(self.files.wav_path), "wb")
                wav_file.setnchannels(self.config.channels)
                wav_file.setsampwidth(self.config.wav_sample_width)
                wav_file.setframerate(self.config.sample_rate)

            if self.files.flac_path is not None:
                import soundfile as sf

                flac_file = sf.SoundFile(
                    str(self.files.flac_path),
                    mode="w",
                    samplerate=self.config.sample_rate,
                    channels=self.config.channels,
                    format="FLAC",
                    subtype=self.config.flac_subtype,
                )

            while True:
                chunk = self._queue.get()
                if chunk is None:
                    break

                if wav_file is not None:
                    wav_file.writeframesraw(chunk)

                if flac_file is not None:
                    flac_file.write(self._pcm_chunk_to_array(chunk))

                self._frames_written += len(chunk) // self.config.frame_size_bytes

        except Exception as exc:
            self._writer_error = exc
            logger.exception("Recording writer failed")
        finally:
            if wav_file is not None:
                wav_file.close()
            if flac_file is not None:
                flac_file.close()

    def _pcm_chunk_to_array(self, chunk: bytes):  # noqa: ANN001
        assert self.config is not None
        import numpy as np

        if self.config.bit_depth == 16:
            data = np.frombuffer(chunk, dtype="<i2")
        else:
            raw = np.frombuffer(chunk, dtype=np.uint8)
            frames = raw.reshape(-1, self.config.channels, 3)
            data = (
                frames[:, :, 0].astype(np.int32)
                | (frames[:, :, 1].astype(np.int32) << 8)
                | (frames[:, :, 2].astype(np.int32) << 16)
            )
            sign_bit = 1 << 23
            data = ((data ^ sign_bit) - sign_bit) << 8

        return data.reshape(-1, self.config.channels)

    def _validate_input_settings(self, sd, device_index: int | None, config: RecordingConfig) -> None:  # noqa: ANN001
        try:
            sd.check_input_settings(
                device=device_index,
                samplerate=config.sample_rate,
                channels=config.channels,
                dtype=config.sounddevice_dtype,
            )
        except Exception as exc:
            raise RuntimeError(
                "Selected microphone cannot open the exact recording format "
                f"({config.sample_rate} Hz, {config.bit_depth}-bit, {config.channels} channel(s)). "
                "Choose a compatible microphone format or change recording settings. "
                "No resampling fallback is used for lossless recording."
            ) from exc

    def _ensure_output_dependencies(self, config: RecordingConfig) -> None:
        if not config.wants_flac:
            return
        try:
            import numpy  # noqa: F401
            import soundfile  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("FLAC recording requires the 'soundfile' and 'numpy' packages.") from exc

    def _verify_outputs(self) -> tuple[bool | None, bool | None]:
        assert self.files is not None
        wav_verified = None
        wav_flac_match = None

        if self.files.wav_path is not None:
            try:
                with wave.open(str(self.files.wav_path), "rb") as wav_file:
                    wav_verified = wav_file.getnframes() == self._frames_written
            except wave.Error:
                wav_verified = False

        if self.files.wav_path is not None and self.files.flac_path is not None:
            try:
                wav_flac_match = self._compare_wav_flac()
            except Exception:
                logger.exception("Could not verify WAV/FLAC equivalence")
                wav_flac_match = False

        return wav_verified, wav_flac_match

    def _compare_wav_flac(self) -> bool:
        assert self.files is not None
        import numpy as np
        import soundfile as sf

        with sf.SoundFile(str(self.files.wav_path)) as wav_file, sf.SoundFile(str(self.files.flac_path)) as flac_file:
            if wav_file.samplerate != flac_file.samplerate or wav_file.channels != flac_file.channels:
                return False

            while True:
                wav_block = wav_file.read(frames=8192, dtype="int32", always_2d=True)
                flac_block = flac_file.read(frames=8192, dtype="int32", always_2d=True)
                if len(wav_block) != len(flac_block):
                    return False
                if len(wav_block) == 0:
                    return True
                if not np.array_equal(wav_block, flac_block):
                    return False

    def _clear_queue(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
