from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from teleprompter_app.config_manager import RecorderSettings
from teleprompter_app.system_profile import CameraProfile

from teleprompter_app.config_manager import RecorderSettings
from teleprompter_app.system_profile import CameraProfile

VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".mov", ".webm"}
AUDIO_SUFFIXES = {".flac", ".mp3", ".wav", ".m4a", ".aac", ".opus"}


def _metadata_args(kind: str, settings: RecorderSettings) -> list[str]:
    created = datetime.now(timezone.utc).isoformat()

    args = [
        "-metadata",
        "title=Teleprompter Recording",
        "-metadata",
        f"comment=Recorded by Teleprompter App ({kind})",
        "-metadata",
        f"creation_time={created}",
        "-metadata",
        "encoder=Teleprompter App + FFmpeg",
    ]

    device = getattr(settings, "video_device", "")
    if device:
        args.extend(["-metadata", f"camera={device}"])

    mic = getattr(settings, "audio_device", "")
    if mic:
        args.extend(["-metadata", f"microphone={mic}"])

    return args


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
        width_str, height_str = res.split("x", 1)
        width, height = int(width_str), int(height_str)
    else:
        width, height = 1280, 720

    # Phase 5: High-res buffer scaling
    rtbuf = "1G" if width >= 3840 else str(settings.rtbufsize)
    queue = 2048 if width >= 3840 else int(settings.thread_queue_size)

    # Phase 4: Timestamp and stability flags
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-y",
        "-fflags",
        "+genpts",
        "-use_wallclock_as_timestamps",
        "1",
        "-f",
        "dshow",
        "-rtbufsize",
        rtbuf,
        "-thread_queue_size",
        str(queue),
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
        ]
    )

    if settings.video_codec == "copy":
        cmd.extend(["-c:v", "copy"])
    elif settings.video_codec in {"libx264", "libx264_lossless"}:
        cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency"])
        if settings.lossless or settings.video_codec == "libx264_lossless":
            cmd.extend(["-crf", "0"])
        else:
            cmd.extend(["-crf", "18", "-pix_fmt", "yuv420p"])
    elif settings.video_codec == "libx264_hq":
        cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"])
    elif settings.video_codec == "h264_nvenc":
        # NVIDIA Hardware Acceleration
        cmd.extend(["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull", "-rc", "constqp", "-qp", "18"])
    elif settings.video_codec == "h264_qsv":
        # Intel QuickSync Acceleration
        cmd.extend(["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "18"])
    elif settings.video_codec == "ffv1":
        cmd.extend(["-c:v", "ffv1", "-level", "3"])
    else:
        cmd.extend(["-c:v", settings.video_codec])

    # Phase 4: Output sync
    cmd.extend(["-fps_mode", "passthrough"])

    cmd.extend(_metadata_args("video", settings))

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
    ]

    if getattr(settings, "audio_bitrate", "") and settings.audio_codec in {"libmp3lame", "aac", "libopus"}:
        cmd.extend(["-b:a", settings.audio_bitrate])

    if getattr(settings, "recording_sample_rate", 0):
        cmd.extend(["-ar", str(settings.recording_sample_rate)])

    if getattr(settings, "recording_channels", 0):
        cmd.extend(["-ac", str(settings.recording_channels)])

    cmd.extend(_metadata_args("audio", settings))

    cmd.append(str(output_path))
    return cmd
