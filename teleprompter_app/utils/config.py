from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AppSettings:
    """User-configurable application settings (Unified UI & Recorder)."""

    # UI / Teleprompter
    font_family: str = "Arial"
    font_size: int = 42
    text_color: str = "#f3f4f6"
    bold: bool = False
    italic: bool = False
    underline: bool = False
    highlight_color: str = "#ffd166"
    highlight_text_color: str = "#101114"
    scroll_speed: int = 65
    input_mode: str = ""
    background_color: str = "#000000"

    # Recognition
    microphone_index: int = -1
    vosk_model_path: str = os.environ.get("VOSK_MODEL_PATH", "models/vosk-model-small-en-us-0.15")
    sample_rate: int = 16000
    audio_block_size: int = 800

    # Recorder - Devices
    video_device: str = ""
    audio_device: str = ""

    # Recorder - Video
    resolution: str = "1280x720"
    fps: int = 30
    pixel_format: str = "yuv420p"
    input_format_kind: str = "pixel_format"
    video_codec: str = "ffv1"
    lossless: bool = True

    # Recorder - Audio
    recording_sample_rate: int = 48000
    recording_bit_depth: int = 16
    recording_channels: int = 1
    audio_codec: str = "flac"

    # Recorder - Global
    recording_mode: str = "audio + video + srt"
    recording_format: str = "both"
    output_dir: str = ""
    container: str = "mkv"
    
    # Recorder - Performance
    rtbufsize: str = "200M"
    thread_queue_size: int = 512
    hw_accel: bool = False
    
    # Recorder - Advanced
    extra_ffmpeg_args: str = ""
    naming_pattern: str = "%d"

    # Preview
    use_camera_background: bool = False
    preview_resolution: str = "360p"

    def updated(self, values: dict[str, Any]) -> "AppSettings":
        valid = {key: value for key, value in values.items() if hasattr(self, key)}
        return replace(self, **valid)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "AppSettings":
        defaults = cls()
        clean = {key: values.get(key, getattr(defaults, key)) for key in defaults.to_dict()}
        return cls(**clean)


class ConfigManager:
    """Load and save settings in the user's profile directory."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path.home() / ".ai_teleprompter" / "settings.json"

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return AppSettings.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
