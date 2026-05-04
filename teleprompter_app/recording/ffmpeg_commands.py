from __future__ import annotations

from pathlib import Path

from teleprompter_app.config_manager import RecorderSettings
from teleprompter_app.system_profile import CameraProfile

VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".mov", ".webm"}
AUDIO_SUFFIXES = {".flac", ".mp3", ".wav", ".m4a", ".aac", ".opus"}


def _input_format_args(settings: RecorderSettings) -> list[str]:
    fmt = (settings.pixel_format or "").strip()
    kind = getattr(settings, "input_format_kind", "pixel_format")

    if not fmt:
        return []

    if kind == "vcodec":
        return ["-vcodec", fmt]

    return ["-pixel_format", fmt]


def build_video_command(
    ffmpeg_path: str,
    settings: RecorderSettings,
    camera: CameraProfile,
    output_path: Path,
    ) -> list[str]:
    output_path = Path(output_path)
    if output_path.suffix.lower() not in VIDEO_SUFFIXES:
        raise RuntimeError(f"Invalid video output path: {output_path}")

    res = settings.resolution or "1280x720"
    if "x" in res:
        width, height = res.split("x", 1)
    else:
        width, height = "1280", "720"

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-y",
        "-f",
        "dshow",
        "-rtbufsize",
        str(settings.rtbufsize),
        "-thread_queue_size",
        str(settings.thread_queue_size),
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(settings.fps),
    ]

    cmd.extend(_input_format_args(settings))

    cmd.extend(
        [
            "-i",
            f"video={camera.ffmpeg_name}",
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            settings.video_codec,
        ]
    )

    if settings.video_codec in {"libx264", "libx265"} and settings.lossless:
        cmd.extend(["-crf", "0", "-preset", "ultrafast"])
    elif settings.video_codec == "ffv1" and settings.lossless:
        cmd.extend(["-level", "3"])

    cmd.append(str(output_path))
    return cmd


def build_audio_command(
    ffmpeg_path: str,
    settings: RecorderSettings,
    output_path: Path,
) -> list[str]:
    output_path = Path(output_path)
    if output_path.suffix.lower() not in AUDIO_SUFFIXES:
        raise RuntimeError(f"Invalid audio output path: {output_path}")

    if not settings.audio_device:
        raise RuntimeError("No audio device selected")

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-y",
        "-f",
        "dshow",
        "-thread_queue_size",
        str(settings.thread_queue_size),
        "-i",
        f"audio={settings.audio_device}",
        "-vn",
        "-c:a",
        settings.audio_codec,
        str(output_path),
    ]

    return cmd
