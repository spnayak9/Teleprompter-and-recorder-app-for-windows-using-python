from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from teleprompter_app.utils.config import AppSettings
from teleprompter_app.system_profile import CameraProfile

VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".mov", ".webm"}
AUDIO_SUFFIXES = {".flac", ".mp3", ".wav", ".m4a", ".aac", ".opus"}


def _metadata_args(kind: str, settings: AppSettings) -> list[str]:
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


def _input_format_args(settings: AppSettings) -> list[str]:
    fmt = (settings.pixel_format or "").strip()
    kind = getattr(settings, "input_format_kind", "pixel_format")

    if not fmt:
        return []

    if kind == "vcodec":
        return ["-vcodec", fmt]

    return ["-pixel_format", fmt]


def build_video_command(
    ffmpeg_path: str,
    settings: AppSettings,
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

def _video_encoder_args(settings: AppSettings) -> list[str]:
    # Phase 5/6/7: Advanced Encoder Routing
    
    if settings.video_encoder_type == "copy":
        return ["-c:v", "copy"]
    
    if settings.video_encoder_type == "hardware" and settings.hardware_encoder:
        enc = settings.hardware_encoder
        if "nvenc" in enc:
            # NVIDIA: High Quality visually lossless
            return ["-c:v", enc, "-preset", "p4", "-rc", "vbr", "-cq", "18", "-b:v", "0", "-pix_fmt", "yuv420p"]
        elif "qsv" in enc:
            # Intel QuickSync
            return ["-c:v", enc, "-preset", "veryfast", "-global_quality", "18"]
        elif "amf" in enc:
            # AMD AMF
            return ["-c:v", enc, "-quality", "quality", "-rc", "cqp", "-qp_i", "18", "-qp_p", "18", "-pix_fmt", "yuv420p"]
        return ["-c:v", enc]

    # Software Encoding (Default fallback)
    codec = settings.software_encoder or settings.video_codec_mode
    if codec == "libx264_hq":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    elif codec == "libx264_lossless":
        return ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-crf", "0"]
    elif codec == "ffv1":
        return ["-c:v", "ffv1", "-level", "3", "-coder", "1", "-context", "1", "-g", "1"]
    elif codec == "mjpeg":
        return ["-c:v", "mjpeg", "-q:v", "2"]
    
    # Generic fallback
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]


def build_video_command(
    ffmpeg_path: str,
    settings: AppSettings,
    camera: CameraProfile,
    output_path: Path,
) -> list[str]:
    output_path = Path(output_path)
    if output_path.suffix.lower() not in VIDEO_SUFFIXES:
        raise RuntimeError(f"Invalid video output path: {output_path}")

    res = settings.resolution or "1280x720"
    if "x" in res:
        try:
            width_str, height_str = res.split("x", 1)
            width, height = int(width_str), int(height_str)
        except ValueError:
            width, height = 1280, 720
    else:
        width, height = 1280, 720

    # High-res buffer scaling
    rtbuf = "1G" if width >= 3840 else str(settings.rtbufsize)
    queue = 2048 if width >= 3840 else int(settings.thread_queue_size)

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-y",
        "-fflags", "+genpts",
        "-use_wallclock_as_timestamps", "1",
        "-f", "dshow",
        "-rtbufsize", rtbuf,
        "-thread_queue_size", str(queue),
        "-video_size", f"{width}x{height}",
        "-framerate", str(settings.fps),
    ]

    cmd.extend(_input_format_args(settings))

    cmd.extend([
        "-i", f"video={camera.ffmpeg_name}",
        "-map", "0:v:0",
        "-an",
    ])

    # Encoder settings
    cmd.extend(_video_encoder_args(settings))

    # Output sync and metadata
    cmd.extend(["-fps_mode", "passthrough"])
    
    # Phase 12: Add encoder metadata
    meta = _metadata_args("video", settings)
    meta.extend([
        "-metadata", f"video_encoder_type={settings.video_encoder_type}",
        "-metadata", f"video_codec_mode={settings.video_codec_mode}",
        "-metadata", f"hardware_encoder={settings.hardware_encoder}",
    ])
    cmd.extend(meta)

    cmd.append(str(output_path))
    return cmd


def build_audio_command(
    ffmpeg_path: str,
    settings: AppSettings,
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
