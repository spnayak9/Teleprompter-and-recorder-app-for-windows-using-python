"""Recording configuration constants and value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RecordingFormat(str, Enum):
    """Supported lossless recording output choices."""

    WAV = "wav"
    FLAC = "flac"
    BOTH = "both"


class BitDepth(int, Enum):
    """Supported PCM bit depths."""

    PCM_16 = 16
    PCM_24 = 24


class ChannelMode(int, Enum):
    """Supported microphone channel layouts."""

    MONO = 1
    STEREO = 2


SUPPORTED_SAMPLE_RATES = (44100, 48000)
DEFAULT_RECORDING_SAMPLE_RATE = 48000
DEFAULT_RECORDING_BIT_DEPTH = BitDepth.PCM_16
DEFAULT_RECORDING_CHANNELS = ChannelMode.MONO
DEFAULT_RECORDING_FORMAT = RecordingFormat.BOTH
MAX_RECORDING_QUEUE_CHUNKS = 128
OS_ENHANCEMENTS_NOTE = (
    "This app records raw PortAudio input and applies no DSP. Windows audio "
    "enhancements such as AGC/noise suppression must be disabled in the OS or "
    "driver control panel when exposed by the device."
)


@dataclass(frozen=True, slots=True)
class RecordingConfig:
    """Runtime settings for bit-preserving microphone recording."""

    sample_rate: int = DEFAULT_RECORDING_SAMPLE_RATE
    bit_depth: int = int(DEFAULT_RECORDING_BIT_DEPTH)
    channels: int = int(DEFAULT_RECORDING_CHANNELS)
    output_format: str = DEFAULT_RECORDING_FORMAT.value

    @property
    def bytes_per_sample(self) -> int:
        return 3 if self.bit_depth == 24 else 2

    @property
    def sounddevice_dtype(self) -> str:
        return "int24" if self.bit_depth == 24 else "int16"

    @property
    def wav_sample_width(self) -> int:
        return self.bytes_per_sample

    @property
    def flac_subtype(self) -> str:
        return "PCM_24" if self.bit_depth == 24 else "PCM_16"

    @property
    def wants_wav(self) -> bool:
        return self.output_format in {RecordingFormat.WAV.value, RecordingFormat.BOTH.value}

    @property
    def wants_flac(self) -> bool:
        return self.output_format in {RecordingFormat.FLAC.value, RecordingFormat.BOTH.value}

    @property
    def frame_size_bytes(self) -> int:
        return self.bytes_per_sample * self.channels

    def validate(self) -> None:
        if self.sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise ValueError("Recording sample rate must be 44100 Hz or 48000 Hz.")
        if self.bit_depth not in {16, 24}:
            raise ValueError("Recording bit depth must be 16-bit or 24-bit PCM.")
        if self.channels not in {1, 2}:
            raise ValueError("Recording channels must be mono or stereo.")
        if self.output_format not in {item.value for item in RecordingFormat}:
            raise ValueError("Recording format must be WAV, FLAC, or Both.")
