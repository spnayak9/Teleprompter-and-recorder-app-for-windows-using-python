"""Real-time audio capture stream."""

from __future__ import annotations

import queue
from dataclasses import dataclass, replace
from threading import Event


@dataclass(slots=True)
class OpenedAudioDevice:
    """Resolved PortAudio input stream settings."""

    device_index: int | None
    sample_rate: int
    block_size: int
    channels: int
    name: str
    host_api: str


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
        self.opened_device: OpenedAudioDevice | None = None

    def start(self) -> OpenedAudioDevice:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Audio capture requires the 'sounddevice' package.") from exc

        resolved = self._resolve_settings(sd)

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

        self._stream = self._open_stream(sd, resolved, callback)
        self._stream.start()
        effective_device = self.opened_device or resolved
        self.opened_device = effective_device
        self.sample_rate = effective_device.sample_rate
        self.block_size = effective_device.block_size
        self.channels = effective_device.channels
        self.device_index = effective_device.device_index
        return effective_device

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

    def _resolve_settings(self, sd):  # noqa: ANN001
        device_index = self._valid_device_index(sd, self.device_index)
        if device_index != self.device_index:
            self.last_status = "Selected microphone was unavailable; using the default input device."

        device_info = sd.query_devices(device_index, "input") if device_index is not None else sd.query_devices(kind="input")
        host_api = self._host_api_name(sd, int(device_info.get("hostapi", 0)))
        channels = min(self.channels, int(device_info.get("max_input_channels", self.channels)) or self.channels)
        channels = max(1, channels)

        sample_rate = self._choose_sample_rate(sd, device_index, channels, device_info)
        block_size = self._block_size_for(sample_rate)

        return OpenedAudioDevice(
            device_index=device_index,
            sample_rate=sample_rate,
            block_size=block_size,
            channels=channels,
            name=str(device_info.get("name", "Default input")),
            host_api=host_api,
        )

    def _valid_device_index(self, sd, device_index: int | None) -> int | None:  # noqa: ANN001
        if device_index is None:
            return None
        try:
            device_info = sd.query_devices(device_index, "input")
        except Exception:
            return None
        if int(device_info.get("max_input_channels", 0)) <= 0:
            return None
        return device_index

    def _choose_sample_rate(self, sd, device_index: int | None, channels: int, device_info: dict) -> int:  # noqa: ANN001
        default_rate = int(float(device_info.get("default_samplerate", self.sample_rate) or self.sample_rate))
        candidates = self._sample_rate_candidates(default_rate)

        last_error: Exception | None = None
        for sample_rate in candidates:
            try:
                sd.check_input_settings(
                    device=device_index,
                    samplerate=sample_rate,
                    channels=channels,
                    dtype="int16",
                )
            except Exception as exc:
                last_error = exc
                continue
            return sample_rate

        raise RuntimeError(
            "Could not open the selected microphone with any supported sample rate. "
            "Try the MME or DirectSound version of the microphone, or lower the Windows "
            f"recording format. Last PortAudio error: {last_error}"
        )

    def _sample_rate_candidates(self, default_rate: int) -> list[int]:
        candidates = [self.sample_rate, default_rate, 48000, 44100, 32000, 16000]
        unique: list[int] = []
        for candidate in candidates:
            if candidate > 0 and candidate not in unique:
                unique.append(candidate)
        return unique

    def _block_size_for(self, sample_rate: int) -> int:
        target = min(self.block_size, max(320, sample_rate // 20))
        return max(320, target)

    def _host_api_name(self, sd, host_api_index: int) -> str:  # noqa: ANN001
        try:
            host_apis = sd.query_hostapis()
        except Exception:
            return "Unknown"
        if 0 <= host_api_index < len(host_apis):
            return str(host_apis[host_api_index].get("name", "Unknown"))
        return "Unknown"

    def _open_stream(self, sd, resolved: OpenedAudioDevice, callback):  # noqa: ANN001
        errors: list[str] = []
        selected_stream = self._try_open_sample_rates(sd, resolved, callback, errors)
        if selected_stream is not None:
            return selected_stream

        if resolved.device_index is not None:
            try:
                fallback = self._resolve_default_settings(sd)
            except Exception as exc:
                errors.append(f"default resolve: {exc}")
            else:
                fallback_stream = self._try_open_sample_rates(sd, fallback, callback, errors)
                if fallback_stream is not None:
                    self.last_status = f"Selected microphone failed; using {fallback.name} ({fallback.host_api})."
                    return fallback_stream

        detail = " | ".join(errors[-6:])
        raise RuntimeError(
            "Could not open the microphone. WASAPI/WDM-KS devices can reject low-latency "
            "streams, fixed buffer sizes, or fixed sample rates; try the MME/DirectSound "
            f"variant if this continues. PortAudio attempts: {detail}"
        )

    def _try_open_sample_rates(
        self,
        sd,  # noqa: ANN001
        resolved: OpenedAudioDevice,
        callback,  # noqa: ANN001
        errors: list[str],
    ):
        candidates = [resolved.sample_rate]
        candidates.extend(
            rate for rate in self._sample_rate_candidates(resolved.sample_rate) if rate not in candidates
        )

        for sample_rate in candidates:
            candidate = replace(
                resolved,
                sample_rate=sample_rate,
                block_size=self._block_size_for(sample_rate),
            )
            if not self._is_sample_rate_supported(sd, candidate):
                errors.append(f"{candidate.name} {sample_rate} Hz rejected by PortAudio probe")
                continue

            stream = self._try_open_variants(sd, candidate, callback, errors)
            if stream is not None:
                return stream

        return None

    def _try_open_variants(
        self,
        sd,  # noqa: ANN001
        resolved: OpenedAudioDevice,
        callback,  # noqa: ANN001
        errors: list[str],
    ):
        variants = [
            (resolved, "low"),
            (resolved, None),
            (replace(resolved, block_size=0), None),
        ]

        for candidate, latency in variants:
            try:
                stream = self._raw_input_stream(sd, candidate, callback, latency=latency)
            except Exception as exc:
                latency_label = latency or "normal"
                errors.append(
                    f"{candidate.name} {candidate.sample_rate} Hz block {candidate.block_size} "
                    f"{latency_label}: {exc}"
                )
                continue

            self.opened_device = candidate
            if candidate.block_size == 0:
                self.last_status = "Fixed buffer open failed; PortAudio selected the buffer size."
            elif latency is None:
                self.last_status = "Low-latency open failed; using normal latency."
            return stream

        return None

    def _is_sample_rate_supported(self, sd, candidate: OpenedAudioDevice) -> bool:  # noqa: ANN001
        try:
            sd.check_input_settings(
                device=candidate.device_index,
                samplerate=candidate.sample_rate,
                channels=candidate.channels,
                dtype="int16",
            )
        except Exception:
            return False
        return True

    def _raw_input_stream(self, sd, resolved: OpenedAudioDevice, callback, latency):  # noqa: ANN001
        kwargs = {
            "samplerate": resolved.sample_rate,
            "blocksize": resolved.block_size,
            "device": resolved.device_index,
            "dtype": "int16",
            "channels": resolved.channels,
            "callback": callback,
        }
        if latency is not None:
            kwargs["latency"] = latency
        return sd.RawInputStream(**kwargs)

    def _resolve_default_settings(self, sd):  # noqa: ANN001
        original_device = self.device_index
        self.device_index = None
        try:
            return self._resolve_settings(sd)
        finally:
            self.device_index = original_device
