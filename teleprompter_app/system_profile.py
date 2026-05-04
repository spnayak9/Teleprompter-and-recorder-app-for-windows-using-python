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


from enum import StrEnum

class EncoderState(StrEnum):
    AVAILABLE = "available"       # detected + verified usable
    UNAVAILABLE = "unavailable"   # detected but failed verification
    UNSUPPORTED = "unsupported"   # not verified and should NOT be selectable

@dataclass(frozen=True, slots=True)
class VideoEncoderProfile:
    """
    Represents a single video encoder detected in the FFmpeg build.
    """
    name: str
    label: str
    kind: str               # "hardware" | "software"
    vendor: str             # "nvidia" | "amd" | "intel" | "software"
    codec_family: str       # "h264" | "hevc" | "av1" | "ffv1" | "mjpeg"
    lossless_capable: bool
    realtime_4k_recommended: bool
    state: EncoderState = EncoderState.UNSUPPORTED
    failure_reason: str = ""

    @property
    def display_label(self) -> str:
        """Human-readable label including verification status for UI display."""
        if self.kind == "software":
            return self.label
        if self.state == EncoderState.AVAILABLE:
            return self.label
        if self.state == EncoderState.UNAVAILABLE:
            return f"{self.label} — not available"
        return f"{self.label} — detected, not verified"

    @property
    def is_usable(self) -> bool:
        """Software encoders always usable. Hardware only if explicitly verified."""
        if self.kind == "software":
            return True
        return self.state == EncoderState.AVAILABLE


@dataclass(frozen=True, slots=True)
class SystemProfile:
    cameras: tuple[CameraProfile, ...] = field(default_factory=tuple)
    audio_inputs: tuple[AudioProfile, ...] = field(default_factory=tuple)
    video_codecs: tuple[str, ...] = field(default_factory=tuple)
    audio_codecs: tuple[str, ...] = field(default_factory=tuple)
    containers: tuple[str, ...] = field(default_factory=tuple)
    hardware_accels: tuple[str, ...] = field(default_factory=tuple)

    # Structured encoder profiles (replaces old string tuples)
    video_encoders: tuple[VideoEncoderProfile, ...] = field(default_factory=tuple)

    # ---------------------------------------------------------------------------
    # Backward-compat properties (deprecated — use video_encoders directly)
    # ---------------------------------------------------------------------------

    @property
    def hardware_video_encoders(self) -> tuple[str, ...]:
        """Names of detected hardware encoders (may include unverified ones)."""
        return tuple(e.name for e in self.video_encoders if e.kind == "hardware")

    @property
    def software_video_encoders(self) -> tuple[str, ...]:
        """Names of detected software encoders."""
        return tuple(e.name for e in self.video_encoders if e.kind == "software")

    # ---------------------------------------------------------------------------
    # Lookup helpers
    # ---------------------------------------------------------------------------

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

    def hardware_encoders(self) -> tuple[VideoEncoderProfile, ...]:
        """Detected hardware encoders (all, including unverified)."""
        return tuple(e for e in self.video_encoders if e.kind == "hardware")

    def usable_hardware_encoders(self) -> tuple[VideoEncoderProfile, ...]:
        """Hardware encoders that have been verified usable."""
        return tuple(e for e in self.video_encoders if e.kind == "hardware" and e.is_usable)

    def software_encoders(self) -> tuple[VideoEncoderProfile, ...]:
        """Software encoders (always usable)."""
        return tuple(e for e in self.video_encoders if e.kind == "software")

    def encoder_by_name(self, name: str) -> VideoEncoderProfile | None:
        for e in self.video_encoders:
            if e.name == name:
                return e
        return None

    def best_hardware_encoder(self) -> VideoEncoderProfile | None:
        """
        Returns the highest-priority verified hardware encoder.
        Priority: NVENC > QSV > AMF
        """
        priority = ["nvenc", "qsv", "amf"]
        usable = self.usable_hardware_encoders()
        for vendor_key in priority:
            for enc in usable:
                if vendor_key in enc.name:
                    return enc
        # No verified one — return first detected as fallback (caller must verify)
        detected = self.hardware_encoders()
        return detected[0] if detected else None

    def with_encoder_verification(
        self, name: str, state: EncoderState, failure_reason: str = ""
    ) -> "SystemProfile":
        """
        Return a new SystemProfile with the given encoder's state updated.
        Used for lazy verification: call this after `verify_encoder_usable()`.
        """
        updated = tuple(
            VideoEncoderProfile(
                name=e.name,
                label=e.label,
                kind=e.kind,
                vendor=e.vendor,
                codec_family=e.codec_family,
                lossless_capable=e.lossless_capable,
                realtime_4k_recommended=e.realtime_4k_recommended,
                state=state if e.name == name else e.state,
                failure_reason=failure_reason if e.name == name else e.failure_reason,
            )
            for e in self.video_encoders
        )
        return SystemProfile(
            cameras=self.cameras,
            audio_inputs=self.audio_inputs,
            video_codecs=self.video_codecs,
            audio_codecs=self.audio_codecs,
            containers=self.containers,
            hardware_accels=self.hardware_accels,
            video_encoders=updated,
        )

    def save_encoder_cache(self) -> None:
        """Persists explicit encoder states (AVAILABLE, UNAVAILABLE) to disk. Ignores UNSUPPORTED."""
        import json
        from pathlib import Path
        cache_path = Path.home() / ".ai_teleprompter" / "encoder_cache.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        
        cache_data = {}
        if cache_path.exists():
            try:
                cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        for e in self.video_encoders:
            # Assertion guard: never save UNSUPPORTED
            if e.state != EncoderState.UNSUPPORTED:
                cache_data[e.name] = {
                    "state": e.state.value,
                    "failure_reason": e.failure_reason,
                }
                
        cache_path.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")

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
            "hardware_accels": list(self.hardware_accels),
            "video_encoders": [
                {
                    "name": e.name,
                    "label": e.label,
                    "kind": e.kind,
                    "vendor": e.vendor,
                    "codec_family": e.codec_family,
                    "lossless_capable": e.lossless_capable,
                    "realtime_4k_recommended": e.realtime_4k_recommended,
                    "state": e.state.value,
                }
                for e in self.video_encoders
            ],
        }
