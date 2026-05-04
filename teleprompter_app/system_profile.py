from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CameraMode:
    width: int
    height: int
    fps: float
    format_name: str
    format_kind: str  # "pixel_format" or "vcodec"

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass(frozen=True, slots=True)
class CameraProfile:
    name: str
    ffmpeg_name: str
    opencv_index: int
    formats: tuple[CameraMode, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class AudioProfile:
    name: str
    ffmpeg_name: str


@dataclass(frozen=True, slots=True)
class SystemProfile:
    cameras: tuple[CameraProfile, ...] = field(default_factory=tuple)
    audio_inputs: tuple[AudioProfile, ...] = field(default_factory=tuple)
    video_codecs: tuple[str, ...] = field(default_factory=tuple)
    audio_codecs: tuple[str, ...] = field(default_factory=tuple)
    containers: tuple[str, ...] = field(default_factory=tuple)
    hardware_video_encoders: tuple[str, ...] = field(default_factory=tuple)
    software_video_encoders: tuple[str, ...] = field(default_factory=tuple)
    hardware_accels: tuple[str, ...] = field(default_factory=tuple)

    def camera_by_ffmpeg_name(self, ffmpeg_name: str) -> CameraProfile | None:
        for cam in self.cameras:
            if cam.ffmpeg_name == ffmpeg_name:
                return cam
        return None

    def camera_by_opencv_index(self, index: int) -> CameraProfile | None:
        for cam in self.cameras:
            if cam.opencv_index == index:
                return cam
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cameras": [
                {
                    "name": cam.name,
                    "ffmpeg_name": cam.ffmpeg_name,
                    "opencv_index": cam.opencv_index,
                    "formats": [
                        {
                            "width": m.width,
                            "height": m.height,
                            "fps": m.fps,
                            "format_name": m.format_name,
                            "format_kind": m.format_kind,
                        }
                        for m in cam.formats
                    ],
                }
                for cam in self.cameras
            ],
            "audio_inputs": [
                {"name": a.name, "ffmpeg_name": a.ffmpeg_name}
                for a in self.audio_inputs
            ],
            "video_codecs": list(self.video_codecs),
            "audio_codecs": list(self.audio_codecs),
            "containers": list(self.containers),
            "hardware_video_encoders": list(self.hardware_video_encoders),
            "software_video_encoders": list(self.software_video_encoders),
            "hardware_accels": list(self.hardware_accels),
        }
