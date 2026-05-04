from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from teleprompter_app.utils.config import AppSettings
from teleprompter_app.system_profile import CameraProfile

logger = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".mov", ".webm"}
AUDIO_SUFFIXES = {".flac", ".mp3", ".wav", ".m4a", ".aac", ".opus"}


# ---------------------------------------------------------------------------
# Encoder argument builders
# ---------------------------------------------------------------------------

def build_video_encoder_args(settings: AppSettings) -> list[str]:
    """
    Build the -c:v ... encoder arguments based on settings.

    Rules:
    - copy: always allowed, pass-through the camera stream unchanged
    - software: use libx264 / ffv1 / mjpeg depending on video_codec_mode
    - hardware: use hardware_encoder — NEVER allowed with empty hardware_encoder
    """
    enc_type = settings.video_encoder_type
    codec_mode = settings.video_codec_mode

    # --- Stream Copy (recommended for 4K) ---
    if enc_type == "copy" or codec_mode == "copy":
        return ["-c:v", "copy"]

    # --- Hardware Encoding ---
    if enc_type == "hardware":
        enc = (settings.hardware_encoder or "").strip()
        if not enc or enc.lower() == "none":
            raise RuntimeError(
                "Hardware encoding selected but hardware_encoder is empty or None. "
                "Select a hardware encoder or switch to Camera Stream Copy."
            )

        if "nvenc" in enc:
            return [
                "-c:v", enc,
                "-preset", "p4",
                "-rc", "vbr",
                "-cq", "19",
                "-b:v", "0",
                "-pix_fmt", "yuv420p",
            ]
        if "qsv" in enc:
            return [
                "-c:v", enc,
                "-global_quality", "20",
            ]
        if "amf" in enc:
            return [
                "-c:v", enc,
                "-quality", "speed",
                "-rc", "cqp",
                "-qp_i", "20",
                "-qp_p", "20",
                "-qp_b", "20",
                "-pix_fmt", "yuv420p",
            ]
        # Generic hardware fallback (e.g. future encoders)
        logger.warning("Unknown hardware encoder %r — using with no extra args", enc)
        return ["-c:v", enc]

    # --- Software Encoding ---
    # Resolve codec name from codec_mode or software_encoder field
    codec = settings.software_encoder or codec_mode or "libx264_hq"

    if codec in ("libx264_hq", "high_quality"):
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]

    if codec in ("libx264", "standard", "libx264_standard"):
        return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p"]

    if codec in ("libx264_lossless", "lossless_h264"):
        return ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-crf", "0"]

    if codec in ("ffv1", "lossless_ffv1"):
        return ["-c:v", "ffv1", "-level", "3", "-g", "1"]

    if codec == "mjpeg":
        return ["-c:v", "mjpeg", "-q:v", "2"]

    # Safe default
    logger.warning("Unrecognized codec_mode %r — falling back to libx264 ultrafast", codec)
    return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p"]


def _effective_encoder_metadata(settings: AppSettings) -> tuple[str, str, str]:
    """
    Return (encoder_type_label, codec_mode_label, hardware_encoder_name)
    that accurately describe what encoder will actually be used.

    Never returns "hardware" as encoder_type if hardware_encoder is empty.
    """
    enc_type = settings.video_encoder_type
    codec_mode = settings.video_codec_mode

    if enc_type == "copy" or codec_mode == "copy":
        return ("copy", "copy", "")

    if enc_type == "hardware":
        enc = (settings.hardware_encoder or "").strip()
        if not enc or enc.lower() == "none":
            raise RuntimeError("Hardware encoding selected but no hardware encoder is configured")
        return ("hardware", codec_mode or enc, enc)

    # Software
    codec = settings.software_encoder or codec_mode or "libx264"
    return ("software", codec, "")


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def _base_metadata_args(kind: str, settings: AppSettings) -> list[str]:
    created = datetime.now(timezone.utc).isoformat()
    args = [
        "-metadata", "title=Teleprompter Recording",
        "-metadata", f"comment=Recorded by Teleprompter App ({kind})",
        "-metadata", f"creation_time={created}",
        "-metadata", "encoder=Teleprompter App + FFmpeg",
    ]
    device = getattr(settings, "video_device", "")
    if device:
        args.extend(["-metadata", f"camera={device}"])
    mic = getattr(settings, "audio_device", "")
    if mic:
        args.extend(["-metadata", f"microphone={mic}"])
    return args


def _video_metadata_args(settings: AppSettings) -> list[str]:
    args = _base_metadata_args("video", settings)
    encoder_type, codec_mode, hw_enc = _effective_encoder_metadata(settings)
    args.extend([
        "-metadata", f"video_encoder_type={encoder_type}",
        "-metadata", f"video_codec_mode={codec_mode}",
        "-metadata", f"hardware_encoder={hw_enc}",
    ])
    return args


# ---------------------------------------------------------------------------
# Input format helper
# ---------------------------------------------------------------------------

def _input_format_args(settings: AppSettings) -> list[str]:
    fmt = (settings.pixel_format or "").strip()
    kind = getattr(settings, "input_format_kind", "pixel_format")
    if not fmt:
        return []
    if kind == "vcodec":
        return ["-vcodec", fmt]
    return ["-pixel_format", fmt]


# ---------------------------------------------------------------------------
# Public command builders
# ---------------------------------------------------------------------------

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
    try:
        width_str, height_str = res.split("x", 1)
        width, height = int(width_str), int(height_str)
    except (ValueError, AttributeError):
        width, height = 1280, 720

    # Scale buffers for high-res
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

    # Encoder args (raises RuntimeError if hardware type with no encoder)
    cmd.extend(build_video_encoder_args(settings))

    # Sync and metadata
    cmd.extend(["-fps_mode", "passthrough"])
    cmd.extend(_video_metadata_args(settings))

    cmd.append(str(output_path))
    logger.debug("Video command: %s", " ".join(cmd))
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
        "-f", "dshow",
        "-thread_queue_size", str(settings.thread_queue_size),
        "-i", f"audio={settings.audio_device}",
        "-vn",
        "-c:a", settings.audio_codec,
    ]

    if getattr(settings, "audio_bitrate", "") and settings.audio_codec in {"libmp3lame", "aac", "libopus"}:
        cmd.extend(["-b:a", settings.audio_bitrate])

    if getattr(settings, "recording_sample_rate", 0):
        cmd.extend(["-ar", str(settings.recording_sample_rate)])

    if getattr(settings, "recording_channels", 0):
        cmd.extend(["-ac", str(settings.recording_channels)])

    cmd.extend(_base_metadata_args("audio", settings))
    cmd.append(str(output_path))
    logger.debug("Audio command: %s", " ".join(cmd))
    return cmd
