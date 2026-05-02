"""Real-time audio capture stream."""

from __future__ import annotations

import queue
from threading import Event


class AudioStream:
    """Low-latency microphone byte stream backed by sounddevice RawInputStream."""

    def __init__(
        self,
        device_index: int | None,
        sample_rate: int = 16000,
        block_size: int = 4000,
        channels: int = 1,
    ) -> None:
        self.device_index = device_index if device_index is not None and device_index >= 0 else None
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.channels = channels
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=4)
        self._stream = None
        self.last_status: str | None = None

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Audio capture requires the 'sounddevice' package.") from exc

        def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if status:
                # Store the newest status without doing blocking work in the real-time callback.
                self.last_status = str(status)
            try:
                self._queue.put_nowait(bytes(indata))
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    return
                self._queue.put_nowait(bytes(indata))

        self._stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            device=self.device_index,
            dtype="int16",
            channels=self.channels,
            latency="low",
            callback=callback,
        )
        self._stream.start()

    def read(self, timeout: float = 0.02) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def chunks(self, stop_event: Event):
        while not stop_event.is_set():
            chunk = self.read(timeout=0.02)
            if chunk:
                yield chunk

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
