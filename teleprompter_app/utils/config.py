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
    video_codec: str = "copy"  # Backward compatibility
    video_codec_mode: str = "copy"
    video_encoder_type: str = "copy"  # copy | software | hardware
    video_acceleration: str = "auto"  # auto | software | hardware
    hardware_encoder: str = ""
    software_encoder: str = "libx264"
    quality_preset: str = "camera_copy"
    hardware_preset: str = "balanced"
    allow_high_risk_lossless: bool = False
    auto_fallback_to_copy: bool = True
    lossless: bool = False  # Backward compatibility

    # Recorder - Audio
    recording_sample_rate: int = 48000
    recording_bit_depth: int = 16
    recording_channels: int = 1
    audio_codec: str = "flac"
    audio_bitrate: str = ""

    # Recorder - Global
    recording_mode: str = "audio + video + srt"
    recording_format: str = "both"
    output_dir: str = ""
    container: str = "mkv"
    recording_video_device: str = ""
    preview_video_device: str = "__same_as_recording__"
    preview_enabled_during_recording: bool = True
    
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
    preview_background_mode: str = "color"   # "none" | "color" | "camera"

    def updated(self, values: dict[str, Any]) -> "AppSettings":
        valid = {key: value for key, value in values.items() if hasattr(self, key)}
        return replace(self, **valid)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "AppSettings":
        defaults = cls()
        clean = {key: values.get(key, getattr(defaults, key)) for key in defaults.to_dict()}
        
        # Migration logic
        if "video_codec" in values and "video_encoder_type" not in values:
            old_codec = values["video_codec"]
            if old_codec == "copy":
                clean["video_encoder_type"] = "copy"
                clean["video_codec_mode"] = "copy"
            elif old_codec in {"libx264_hq", "libx264_lossless", "ffv1"}:
                clean["video_encoder_type"] = "software"
                clean["video_codec_mode"] = old_codec
            elif old_codec in {"h264_nvenc", "h264_qsv", "h264_amf"}:
                clean["video_encoder_type"] = "hardware"
                clean["hardware_encoder"] = old_codec
                clean["video_codec_mode"] = old_codec
                
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
