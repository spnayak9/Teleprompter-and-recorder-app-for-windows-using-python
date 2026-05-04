from __future__ import annotations

import logging
import re
import subprocess

from teleprompter_app.camera_mapper import detect_opencv_cameras
from teleprompter_app.system_profile import (
    AudioProfile,
    CameraMode,
    CameraProfile,
    SystemProfile,
)

log = logging.getLogger(__name__)


_DEVICE_RE = re.compile(r'\[(?:dshow|in#\d+).*?\]\s+"(.+?)"\s+\((video|audio)\)', re.IGNORECASE)

_MODE_PIXEL_RE = re.compile(
    r"pixel_format=(?P<fmt>[a-zA-Z0-9_]+).*?"
    r"min s=(?P<w>\d+)x(?P<h>\d+).*?"
    r"fps=(?P<fps>[0-9.]+)",
    re.IGNORECASE,
)

_MODE_VCODEC_RE = re.compile(
    r"vcodec=(?P<fmt>[a-zA-Z0-9_]+).*?"
    r"min s=(?P<w>\d+)x(?P<h>\d+).*?"
    r"fps=(?P<fps>[0-9.]+)",
    re.IGNORECASE,
)


def _run_ffmpeg(args: list[str], timeout: int = 20) -> str:
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _ffmpeg_list_devices(ffmpeg_path: str) -> tuple[list[str], list[str]]:
    output = _run_ffmpeg(
        [
            ffmpeg_path,
            "-hide_banner",
            "-list_devices",
            "true",
            "-f",
            "dshow",
            "-i",
            "dummy",
        ],
        timeout=20,
    )

    video: list[str] = []
    audio: list[str] = []

    for match in _DEVICE_RE.finditer(output):
        name = match.group(1).strip()
        kind = match.group(2).lower()
        if kind == "video":
            video.append(name)
        elif kind == "audio":
            audio.append(name)

    return video, audio


def _ffmpeg_list_camera_modes(ffmpeg_path: str, ffmpeg_device_name: str) -> tuple[CameraMode, ...]:
    output = _run_ffmpeg(
        [
            ffmpeg_path,
            "-hide_banner",
            "-f",
            "dshow",
            "-list_options",
            "true",
            "-i",
            f"video={ffmpeg_device_name}",
        ],
        timeout=25,
    )

    modes: set[tuple[int, int, float, str, str]] = set()

    for line in output.splitlines():
        match = _MODE_PIXEL_RE.search(line)
        if match:
            modes.add(
                (
                    int(match.group("w")),
                    int(match.group("h")),
                    float(match.group("fps")),
                    match.group("fmt").strip(),
                    "pixel_format",
                )
            )
            continue

        match = _MODE_VCODEC_RE.search(line)
        if match:
            modes.add(
                (
                    int(match.group("w")),
                    int(match.group("h")),
                    float(match.group("fps")),
                    match.group("fmt").strip(),
                    "vcodec",
                )
            )

    return tuple(
        CameraMode(
            width=w,
            height=h,
            fps=fps,
            format_name=fmt,
            format_kind=kind,
        )
        for w, h, fps, fmt, kind in sorted(
            modes,
            key=lambda x: (x[0] * x[1], x[2], x[3], x[4]),
            reverse=True,
        )
    )


def _ffmpeg_codecs(ffmpeg_path: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    output = _run_ffmpeg([ffmpeg_path, "-hide_banner", "-codecs"], timeout=20)

    video: list[str] = []
    audio: list[str] = []

    for line in output.splitlines():
        # FFmpeg codec table shape:
        # DEV.LS h264 ...
        if len(line) < 8:
            continue

        flags = line[:7]
        parts = line[7:].strip().split()
        if not parts:
            continue

        codec = parts[0]

        can_encode = "E" in flags
        is_video = "V" in flags
        is_audio = "A" in flags

        if not can_encode:
            continue

        if is_video:
            video.append(codec)
        elif is_audio:
            audio.append(codec)

    return tuple(sorted(set(video))), tuple(sorted(set(audio)))


def _ffmpeg_muxers(ffmpeg_path: str) -> tuple[str, ...]:
    output = _run_ffmpeg([ffmpeg_path, "-hide_banner", "-muxers"], timeout=20)

    muxers: list[str] = []

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("E "):
            continue

        parts = stripped.split()
        if len(parts) >= 2:
            muxers.append(parts[1])

    return tuple(sorted(set(muxers)))


def probe_system(ffmpeg_path: str = "ffmpeg") -> SystemProfile:
    """
    Production rule:
    Run this exactly once at application startup.
    """
    log.info("Probing system capabilities once...")

    ffmpeg_video_devices, ffmpeg_audio_devices = _ffmpeg_list_devices(ffmpeg_path)
    opencv_cameras = detect_opencv_cameras(max_indices=5)

    cameras: list[CameraProfile] = []

    for i, ffmpeg_name in enumerate(ffmpeg_video_devices):
        if i >= len(opencv_cameras):
            log.warning(
                "FFmpeg camera %r has no matching OpenCV index. It will not be used for preview.",
                ffmpeg_name,
            )
            continue

        formats = _ffmpeg_list_camera_modes(ffmpeg_path, ffmpeg_name)
        opencv_index = opencv_cameras[i].index

        cameras.append(
            CameraProfile(
                name=ffmpeg_name,
                ffmpeg_name=ffmpeg_name,
                opencv_index=opencv_index,
                formats=formats,
            )
        )

    video_codecs, audio_codecs = _ffmpeg_codecs(ffmpeg_path)
    containers = _ffmpeg_muxers(ffmpeg_path)

    profile = SystemProfile(
        cameras=tuple(cameras),
        audio_inputs=tuple(
            AudioProfile(name=name, ffmpeg_name=name)
            for name in ffmpeg_audio_devices
        ),
        video_codecs=video_codecs,
        audio_codecs=audio_codecs,
        containers=containers,
    )

    log.info(
        "System profile ready: %s cameras, %s audio inputs",
        len(profile.cameras),
        len(profile.audio_inputs),
    )

    return profile
