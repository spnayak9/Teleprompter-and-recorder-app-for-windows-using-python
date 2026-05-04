from __future__ import annotations

import logging
import re
import subprocess

from pathlib import Path
from teleprompter_app.camera_mapper import detect_opencv_cameras
from teleprompter_app.system_profile import (
    AudioProfile,
    CameraMode,
    CameraProfile,
    SystemProfile,
)

log = logging.getLogger(__name__)


_DEVICE_RE = re.compile(r'\[(?:dshow|in#\d+).*?\]\s+"(.+?)"\s+\((video|audio)\)', re.IGNORECASE)

FPS_RE = re.compile(r"fps=\s*(?P<fps>[0-9.]+)", re.IGNORECASE)
SIZE_RE = re.compile(r"(?:min|max)?\s*s=(?P<w>\d+)x(?P<h>\d+)", re.IGNORECASE)
PIXEL_RE = re.compile(r"pixel_format=(?P<fmt>[a-zA-Z0-9_]+)", re.IGNORECASE)
VCODEC_RE = re.compile(r"vcodec=(?P<fmt>[a-zA-Z0-9_]+)", re.IGNORECASE)
INTERVAL_RE = re.compile(r"(?:min|max)\s*interval=(?P<interval>\d+)", re.IGNORECASE)

HARDWARE_ENCODERS = {
    "h264_nvenc": "NVIDIA H.264 NVENC",
    "hevc_nvenc": "NVIDIA HEVC NVENC",
    "av1_nvenc": "NVIDIA AV1 NVENC",
    "h264_qsv": "Intel H.264 Quick Sync",
    "hevc_qsv": "Intel HEVC Quick Sync",
    "av1_qsv": "Intel AV1 Quick Sync",
    "h264_amf": "AMD H.264 AMF",
    "hevc_amf": "AMD HEVC AMF",
    "av1_amf": "AMD AV1 AMF",
}

SOFTWARE_ENCODERS = {
    "libx264": "H.264 Software",
    "libx265": "HEVC Software",
    "ffv1": "FFV1 Lossless",
    "mjpeg": "MJPEG Software",
}


def interval_100ns_to_fps(interval: int) -> float:
    if interval <= 0:
        return 0.0
    return round(10_000_000 / interval, 3)


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_")


def _write_camera_probe_dump(camera_name: str, output: str) -> None:
    try:
        log_dir = Path.cwd() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"camera_modes_{_safe_filename(camera_name)}.txt"
        path.write_text(output, encoding="utf-8", errors="replace")
        log.info("Saved camera mode probe dump: %s", path)
    except Exception:
        log.exception("Could not save camera probe dump")


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

    _write_camera_probe_dump(ffmpeg_device_name, output)

    modes: set[tuple[int, int, float, str, str]] = set()

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        pixel_match = PIXEL_RE.search(line)
        vcodec_match = VCODEC_RE.search(line)
        fmt = ""
        kind = ""

        if pixel_match:
            fmt = pixel_match.group("fmt").strip()
            kind = "pixel_format"
        elif vcodec_match:
            fmt = vcodec_match.group("fmt").strip()
            kind = "vcodec"
        else:
            continue

        sizes = SIZE_RE.findall(line)
        if not sizes:
            continue

        fps_values = [float(m.group("fps")) for m in FPS_RE.finditer(line)]
        intervals = [int(m.group("interval")) for m in INTERVAL_RE.finditer(line)]
        for interval in intervals:
            fps_values.append(interval_100ns_to_fps(interval))

        for w_str, h_str in sizes:
            width, height = int(w_str), int(h_str)
            for fps in fps_values:
                if fps > 0:
                    modes.add((width, height, fps, fmt, kind))

    # Phase 3: Secondary verification for known resolutions
    # Common candidate FPS for high-res modes
    candidates = [60.0, 30.0, 24.0, 15.0]
    unique_resolutions = sorted({(m[0], m[1], m[3], m[4]) for m in modes})

    for w, h, fmt, kind in unique_resolutions:
        # Only verify high-res modes if they have very few FPS values
        current_fps = {m[2] for m in modes if m[0] == w and m[1] == h}
        if len(current_fps) < 2:
            for cand_fps in candidates:
                if cand_fps not in current_fps:
                    if _verify_camera_mode(ffmpeg_path, ffmpeg_device_name, w, h, cand_fps, fmt, kind):
                        modes.add((w, h, cand_fps, fmt, kind))

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


def _verify_camera_mode(
    ffmpeg_path: str,
    device_name: str,
    width: int,
    height: int,
    fps: float,
    fmt: str,
    kind: str,
) -> bool:
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-f",
        "dshow",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(fps),
    ]
    if kind == "vcodec":
        cmd.extend(["-vcodec", fmt])
    else:
        cmd.extend(["-pixel_format", fmt])

    cmd.extend(["-t", "0.5", "-i", f"video={device_name}", "-f", "null", "-"])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        # If FFmpeg starts successfully (even if it stops due to timeout or short duration), it's often a valid mode
        # We check if there are no major "Could not set" or "Unsupported" errors in the first few lines
        if "Could not set" in proc.stderr or "Unsupported" in proc.stderr:
            return False
        return proc.returncode == 0 or "Stream #0:0" in proc.stderr
    except Exception:
        return False


def _ffmpeg_codecs(ffmpeg_path: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    output = _run_ffmpeg([ffmpeg_path, "-hide_banner", "-codecs"], timeout=20)

    video: list[str] = []
    audio: list[str] = []
    hardware_video: list[str] = []
    software_video: list[str] = []

    for line in output.splitlines():
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
            if codec in HARDWARE_ENCODERS:
                hardware_video.append(codec)
            elif codec in SOFTWARE_ENCODERS:
                software_video.append(codec)
        elif is_audio:
            audio.append(codec)

    return (
        tuple(sorted(set(video))),
        tuple(sorted(set(audio))),
        tuple(sorted(set(hardware_video))),
        tuple(sorted(set(software_video))),
    )


def _ffmpeg_hwaccels(ffmpeg_path: str) -> tuple[str, ...]:
    output = _run_ffmpeg([ffmpeg_path, "-hide_banner", "-hwaccels"], timeout=15)
    accels: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if line and not line.startswith("Hardware"):
            accels.append(line)
    return tuple(sorted(set(accels)))


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

    video_codecs, audio_codecs, hw_video, sw_video = _ffmpeg_codecs(ffmpeg_path)
    containers = _ffmpeg_muxers(ffmpeg_path)
    hw_accels = _ffmpeg_hwaccels(ffmpeg_path)

    profile = SystemProfile(
        cameras=tuple(cameras),
        audio_inputs=tuple(
            AudioProfile(name=name, ffmpeg_name=name)
            for name in ffmpeg_audio_devices
        ),
        video_codecs=video_codecs,
        audio_codecs=audio_codecs,
        containers=containers,
        hardware_video_encoders=hw_video,
        software_video_encoders=sw_video,
        hardware_accels=hw_accels,
    )

    for cam in profile.cameras:
        log.info("Camera modes for %s:", cam.ffmpeg_name)
        for mode in cam.formats:
            log.info(
                "  %s fps=%s format=%s kind=%s",
                mode.resolution,
                mode.fps,
                mode.format_name,
                mode.format_kind,
            )

    log.info(
        "System profile ready: %s cameras, %s audio inputs, %s hw encoders, %s sw encoders",
        len(profile.cameras),
        len(profile.audio_inputs),
        len(profile.hardware_video_encoders),
        len(profile.software_video_encoders),
    )
    if profile.hardware_video_encoders:
        log.info("Hardware encoders: %s", ", ".join(profile.hardware_video_encoders))
    if profile.software_video_encoders:
        log.info("Software encoders: %s", ", ".join(profile.software_video_encoders))
    if profile.hardware_accels:
        log.info("Hardware accelerators: %s", ", ".join(profile.hardware_accels))

    return profile
