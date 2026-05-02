"""Microphone device discovery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MicrophoneDevice:
    """Input-capable audio device."""

    index: int
    name: str
    host_api: str
    max_input_channels: int
    default_sample_rate: int

    @property
    def label(self) -> str:
        return f"{self.name} ({self.host_api})"


class MicrophoneManager:
    """List audio input devices through sounddevice/PortAudio."""

    def list_input_devices(self) -> list[MicrophoneDevice]:
        try:
            import sounddevice as sd
        except ImportError:
            return []

        devices: list[MicrophoneDevice] = []
        host_apis = sd.query_hostapis()

        for index, device in enumerate(sd.query_devices()):
            max_channels = int(device.get("max_input_channels", 0))
            if max_channels <= 0:
                continue

            host_api_index = int(device.get("hostapi", 0))
            host_api = host_apis[host_api_index]["name"] if host_api_index < len(host_apis) else "Unknown"
            devices.append(
                MicrophoneDevice(
                    index=index,
                    name=str(device.get("name", f"Device {index}")),
                    host_api=host_api,
                    max_input_channels=max_channels,
                    default_sample_rate=int(device.get("default_samplerate", 16000)),
                )
            )

        return devices
