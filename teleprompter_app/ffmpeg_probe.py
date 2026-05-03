"""Probe ffmpeg for runtime capabilities.

Provides a small helper to query the local `ffmpeg` executable and return a
structured `FFmpegCapabilities` object containing encoders, muxers, pixel
formats and available hardware accelerations.

This module is intentionally conservative in parsing and returns lists of
names that the UI can use to populate constrained selectors.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict


@dataclass(slots=True)
class FFmpegCapabilities:
    encoders: List[str]
    video_encoders: List[str]
    audio_encoders: List[str]
    muxers: List[str]
    pixel_formats: List[str]
    hw_accels: List[str]


def _find_ffmpeg() -> str:
    # Prefer explicit ffmpeg on PATH; fall back to plain 'ffmpeg'
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    # last resort: raise
    raise RuntimeError("ffmpeg not found on PATH")


def _run(cmd: List[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
        return (p.stdout or "") + (p.stderr or "")
    except Exception:
        return ""


def probe_ffmpeg(ffmpeg_path: Optional[str] = None) -> FFmpegCapabilities:
    """Run a few ffmpeg commands and extract capability lists.

    The function is defensive: if a particular ffmpeg invocation fails the
    returned lists may be empty but the call will not raise unless ffmpeg is
    missing entirely.
    """
    path = ffmpeg_path or _find_ffmpeg()

    # Encoders
    enc_out = _run([path, "-encoders"]) or ""
    encoders: List[str] = []
    video_enc: List[str] = []
    audio_enc: List[str] = []
    for line in enc_out.splitlines():
        # match lines like: " V..... libx264             H.264 / AVC / MPEG-4 AVC / ..."
        m = re.match(r"^\s*([A-Z\.]+)\s+(\S+)", line)
        if not m:
            continue
        flags, name = m.group(1), m.group(2)
        encoders.append(name)
        if "V" in flags:
            video_enc.append(name)
        if "A" in flags:
            audio_enc.append(name)

    # Muxers
    mux_out = _run([path, "-muxers"]) or _run([path, "-formats"]) or ""
    muxers: List[str] = []
    for line in mux_out.splitlines():
        # lines like: " E mkv,webm             Matroska output"
        m = re.match(r"^\s*[DEI]*\s*(\S+)", line)
        if not m:
            continue
        # some entries are comma separated; take the primary name
        name = m.group(1).split(",")[0].strip()
        if name and name not in muxers:
            muxers.append(name)

    # Pixel formats
    pix_out = _run([path, "-pix_fmts"]) or ""
    pix: List[str] = []
    for line in pix_out.splitlines():
        # typical table rows: "  IO... name   description"
        m = re.match(r"^\s*[OI\.]+\s+(\S+)", line)
        if not m:
            continue
        name = m.group(1).strip()
        if name and name not in pix:
            pix.append(name)

    # Hardware accelerations
    hw_out = _run([path, "-hwaccels"]) or ""
    hw: List[str] = []
    for line in hw_out.splitlines():
        name = line.strip()
        if not name:
            continue
        # strip non-alphanumeric prefix lines
        if re.match(r"^[A-Za-z0-9_-]+$", name) and name not in hw:
            hw.append(name)

    return FFmpegCapabilities(
        encoders=sorted(set(encoders)),
        video_encoders=sorted(set(video_enc)),
        audio_encoders=sorted(set(audio_enc)),
        muxers=sorted(set(muxers)),
        pixel_formats=sorted(set(pix)),
        hw_accels=sorted(set(hw)),
    )


@dataclass
class CameraMode:
    width: int
    height: int
    fps: float
    formats: Set[str] = field(default_factory=set)


@dataclass
class CameraProfile:
    name: str
    device_path: str
    opencv_index: int = -1
    modes: List[CameraMode] = field(default_factory=list)


@dataclass
class AudioProfile:
    name: str
    device_path: str
    sample_rates: Set[int] = field(default_factory=set)
    channels: Set[int] = field(default_factory=set)


@dataclass
class SystemProbe:
    cameras: List[CameraProfile] = field(default_factory=list)
    audios: List[AudioProfile] = field(default_factory=list)
    ffmpeg: Optional[FFmpegCapabilities] = None


def _run_quiet(cmd: List[str], timeout: int = 8) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="ignore", timeout=timeout)
        return (p.stderr or "") + "\n" + (p.stdout or "")
    except Exception:
        return ""


def _parse_list_devices(output: str) -> Dict[str, List[str]]:
    video = []
    audio = []
    context = None
    for ln in (output or "").splitlines():
        if 'DirectShow video devices' in ln or 'Video devices' in ln:
            context = 'video'
            continue
        if 'DirectShow audio devices' in ln or 'Audio devices' in ln:
            context = 'audio'
            continue
        m = re.search(r'"(.+?)"', ln)
        if m and context == 'video':
            video.append(m.group(1).strip())
        if m and context == 'audio':
            audio.append(m.group(1).strip())
    return {'video': video, 'audio': audio}


def _parse_dshow_options(output: str) -> List[CameraMode]:
    modes = []
    for ln in (output or "").splitlines():
        m = re.search(r'(\d{2,5})\s*[xX]\s*(\d{2,5})', ln)
        if not m:
            continue
        w = int(m.group(1))
        h = int(m.group(2))
        m2 = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*fps', ln, re.I)
        fps = float(m2.group(1)) if m2 else 0.0
        fmts = set()
        m3 = re.search(r'pix(?:el_)?fmt=(\w+)', ln, re.I)
        if m3:
            fmts.add(m3.group(1))
        else:
            m4 = re.search(r'\(([^\)]+)\)', ln)
            if m4:
                fmt_guess = m4.group(1).strip()
                fmts.add(fmt_guess)
        modes.append(CameraMode(width=w, height=h, fps=fps, formats=fmts))
    # dedupe
    uniq = {}
    for m in modes:
        key = (m.width, m.height, round(m.fps, 3))
        if key in uniq:
            uniq[key].formats.update(m.formats)
        else:
            uniq[key] = m
    return list(uniq.values())


def probe_system(ffmpeg_path: Optional[str] = None, timeout: int = 8) -> SystemProbe:
    """High-level system probe: ffmpeg capabilities + DirectShow device modes (Windows).

    Returns a SystemProbe object containing camera/audio profiles and ffmpeg capabilities.
    """
    sp = SystemProbe()
    try:
        ff = probe_ffmpeg(ffmpeg_path)
        sp.ffmpeg = ff
    except Exception:
        sp.ffmpeg = None

    try:
        path = ffmpeg_path or ("ffmpeg" if shutil.which("ffmpeg") else None)
        if not path:
            return sp
        out = _run_quiet([path, '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'], timeout=timeout)
        devs = _parse_list_devices(out)
        cams = []
        for i, name in enumerate(devs.get('video', [])):
            opts = _run_quiet([path, '-f', 'dshow', '-list_options', 'true', '-i', f'video={name}'], timeout=timeout)
            modes = _parse_dshow_options(opts)
            cams.append(CameraProfile(name=name, device_path=name, modes=modes, opencv_index=i))
        auds = []
        for name in devs.get('audio', []):
            opts = _run_quiet([path, '-f', 'dshow', '-list_options', 'true', '-i', f'audio={name}'], timeout=timeout)
            rates = set()
            channels = set()
            for ln in (opts or "").splitlines():
                m = re.search(r'(\d{3,5})\s*Hz', ln)
                if m:
                    try:
                        rates.add(int(m.group(1)))
                    except Exception:
                        pass
                m2 = re.search(r'channels?=(\d+)', ln)
                if m2:
                    try:
                        channels.add(int(m2.group(1)))
                    except Exception:
                        pass
            auds.append(AudioProfile(name=name, device_path=name, sample_rates=rates, channels=channels))
        sp.cameras = cams
        sp.audios = auds
    except Exception:
        # fail gracefully and return whatever we have
        pass

    return sp


__all__ = ["FFmpegCapabilities", "probe_ffmpeg", "probe_system", "CameraProfile", "CameraMode", "AudioProfile", "SystemProbe"]
