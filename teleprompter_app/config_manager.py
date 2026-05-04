"""Persist and load recorder / UI configuration.

This module provides a lightweight JSON-backed configuration manager used by
the recorder and configuration UI. Settings are stored in the user's profile
directory under `.ai_teleprompter/recorder_config.json` by default.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field, replace
from pathlib import Path
import json
from typing import Any, Dict, Optional


@dataclass(slots=True)
class RecorderSettings:
    # Devices
    video_device: str = ""
    audio_device: str = ""

    # Video
    resolution: str = "1280x720"
    fps: int = 30
    pixel_format: str = "yuv420p"
    input_format_kind: str = "pixel_format"
    video_codec: str = "ffv1"
    lossless: bool = True

    # Audio
    sample_rate: int = 48000
    channels: int = 1
    audio_codec: str = "flac"

    # Recording mode (controls which streams to capture)
    recording_mode: str = "audio + video + srt"

    # Background / preview
    use_camera_background: bool = False
    preview_resolution: str = "360p"
    background_color: str = "#000000"

    # Performance
    rtbufsize: str = "200M"
    thread_queue_size: int = 512
    hw_accel: bool = False

    # Output
    container: str = "mkv"
    output_dir: str = ""
    naming_pattern: str = "%d"

    # Advanced
    extra_ffmpeg_args: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecorderSettings":
        defaults = cls()
        clean = {k: data.get(k, getattr(defaults, k)) for k in defaults.to_dict()}
        return cls(**clean)


class ConfigManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else Path.home() / ".ai_teleprompter" / "recorder_config.json"

    def load(self) -> RecorderSettings:
        if not self.path.exists():
            return RecorderSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return RecorderSettings.from_dict(data)
        except Exception:
            return RecorderSettings()

    def save(self, settings: RecorderSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")


__all__ = ["RecorderSettings", "ConfigManager"]
