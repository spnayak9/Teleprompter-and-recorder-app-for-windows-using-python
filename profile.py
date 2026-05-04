"""
profile.py — System diagnostics for the Teleprompter App.

Run from the project root:
    teleprompter\Scripts\python profile.py

Outputs:
    full_system_profile.txt        — human-readable summary
    logs/ffmpeg_encoders.txt       — full ffmpeg -encoders output
    logs/ffmpeg_hwaccels.txt       — full ffmpeg -hwaccels output
    logs/ffmpeg_encoder_verify.txt — hardware encoder verify results
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")
OUTPUT_FILE = Path("full_system_profile.txt")
LOG_DIR = Path("logs")

HARDWARE_ENCODER_CANDIDATES = [
    "h264_nvenc",
    "hevc_nvenc",
    "h264_qsv",
    "hevc_qsv",
    "h264_amf",
    "hevc_amf",
    "av1_amf",
    "av1_qsv",
    "av1_nvenc",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run(args: list[str], timeout: int = 30) -> str:
    try:
        p = subprocess.run(
            args, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return (p.stdout or "") + "\n" + (p.stderr or "")
    except FileNotFoundError:
        return f"[ERROR] Command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return f"[ERROR] Timed out after {timeout}s"
    except Exception as e:
        return f"[ERROR] {e}"


def _save(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")


def _section(title: str, width: int = 72) -> str:
    return f"\n{'=' * width}\n{title}\n{'=' * width}\n"


# ---------------------------------------------------------------------------
# System info
# ---------------------------------------------------------------------------
def system_info() -> str:
    try:
        import psutil
        cpu_phys = psutil.cpu_count(logical=False)
        cpu_log = psutil.cpu_count(logical=True)
        freq = psutil.cpu_freq()
        ram = psutil.virtual_memory()
        ram_total = ram.total / 1024 ** 3
        ram_avail = ram.available / 1024 ** 3
        freq_str = f"Max Frequency: {freq.max:.0f} MHz" if freq else "Frequency: N/A"
        cpu_str = (
            f"Physical cores: {cpu_phys}\n"
            f"Logical cores:  {cpu_log}\n"
            f"{freq_str}\n"
            f"RAM: {ram_total:.2f} GB total, {ram_avail:.2f} GB available"
        )
    except ImportError:
        cpu_str = "psutil not installed — CPU/RAM info unavailable"

    return (
        _section("SYSTEM INFO") +
        f"OS: {platform.system()} {platform.release()} ({platform.version()})\n"
        f"Architecture: {platform.machine()}\n"
        f"Python: {sys.version}\n"
        f"{cpu_str}\n"
    )


# ---------------------------------------------------------------------------
# FFmpeg version
# ---------------------------------------------------------------------------
def ffmpeg_version() -> str:
    out = _run([FFMPEG_PATH, "-hide_banner", "-version"])
    return _section("FFMPEG VERSION") + out


# ---------------------------------------------------------------------------
# Hardware accelerators
# ---------------------------------------------------------------------------
def ffmpeg_hwaccels() -> str:
    out = _run([FFMPEG_PATH, "-hide_banner", "-hwaccels"])
    _save(LOG_DIR / "ffmpeg_hwaccels.txt", out)
    return _section("FFMPEG HARDWARE ACCELERATION APIS (ffmpeg -hwaccels)") + out


# ---------------------------------------------------------------------------
# Encoder list
# ---------------------------------------------------------------------------
def ffmpeg_encoders() -> str:
    out = _run([FFMPEG_PATH, "-hide_banner", "-encoders"])
    _save(LOG_DIR / "ffmpeg_encoders.txt", out)

    # Extract and highlight known hardware/software encoders
    relevant_hw = []
    relevant_sw = []
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        name = parts[1]
        if name in HARDWARE_ENCODER_CANDIDATES:
            relevant_hw.append(f"  [HW] {name:20s} {' '.join(parts[2:])}")
        for sw in ("libx264", "libx265", "ffv1", "mjpeg", "libopus", "aac", "flac"):
            if name == sw:
                relevant_sw.append(f"  [SW] {name:20s} {' '.join(parts[2:])}")

    summary = ""
    if relevant_hw:
        summary += "\nDetected hardware encoders:\n" + "\n".join(relevant_hw) + "\n"
    else:
        summary += "\nNo known hardware encoders found in ffmpeg -encoders output.\n"
    if relevant_sw:
        summary += "\nDetected software encoders:\n" + "\n".join(relevant_sw) + "\n"

    return (
        _section("FFMPEG ENCODERS (ffmpeg -encoders — saved to logs/ffmpeg_encoders.txt)") +
        summary +
        "\nFull output saved to logs/ffmpeg_encoders.txt\n"
    )


# ---------------------------------------------------------------------------
# Hardware encoder verification
# ---------------------------------------------------------------------------
def verify_hardware_encoders() -> str:
    lines = [_section("HARDWARE ENCODER VERIFICATION (1-second test encode)")]
    results = {}

    for enc in HARDWARE_ENCODER_CANDIDATES:
        cmd = [
            FFMPEG_PATH, "-hide_banner", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30",
            "-t", "1",
            "-c:v", enc,
            "-f", "null", "-",
        ]
        try:
            p = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=20,
            )
            if p.returncode == 0:
                results[enc] = ("PASS", "")
                lines.append(f"  ✓ PASS  {enc}")
            else:
                stderr_tail = (p.stderr or "")[-300:].strip()
                results[enc] = ("FAIL", stderr_tail)
                lines.append(f"  ✗ FAIL  {enc}")
                lines.append(f"          {stderr_tail[:120]}")
        except FileNotFoundError:
            results[enc] = ("SKIP", "ffmpeg not found")
            lines.append(f"  - SKIP  {enc}  (ffmpeg not found)")
        except subprocess.TimeoutExpired:
            results[enc] = ("TIMEOUT", "")
            lines.append(f"  - TIMEOUT  {enc}")
        except Exception as e:
            results[enc] = ("ERROR", str(e))
            lines.append(f"  - ERROR  {enc}: {e}")

    # Save detailed verify log
    verify_log = "\n".join(
        f"{enc}: {status} {reason}"
        for enc, (status, reason) in results.items()
    )
    _save(LOG_DIR / "ffmpeg_encoder_verify.txt", verify_log)

    passcount = sum(1 for s, _ in results.values() if s == "PASS")
    lines.append(f"\nResult: {passcount}/{len(HARDWARE_ENCODER_CANDIDATES)} hardware encoders verified usable.")
    lines.append("Full results saved to logs/ffmpeg_encoder_verify.txt")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Camera modes
# ---------------------------------------------------------------------------
def camera_modes() -> str:
    out = _run([
        FFMPEG_PATH, "-hide_banner", "-list_devices", "true",
        "-f", "dshow", "-i", "dummy",
    ])

    import re
    device_re = re.compile(r'\[(?:dshow|in#\d+).*?\]\s+"(.+?)"\s+\((video|audio)\)', re.IGNORECASE)
    video_devices = []
    for m in device_re.finditer(out):
        if m.group(2).lower() == "video":
            video_devices.append(m.group(1).strip())

    section = _section(f"CAMERA DEVICES ({len(video_devices)} found)")
    for name in video_devices:
        section += f"  • {name}\n"

    for name in video_devices:
        section += f"\nModes for: {name}\n"
        mode_out = _run([
            FFMPEG_PATH, "-hide_banner", "-f", "dshow",
            "-list_options", "true", "-i", f"video={name}",
        ], timeout=30)
        for line in mode_out.splitlines():
            if any(k in line for k in ("pixel_format", "vcodec", "min", "max")):
                section += f"  {line.strip()}\n"

    return section


# ---------------------------------------------------------------------------
# Audio devices
# ---------------------------------------------------------------------------
def audio_devices() -> str:
    out = _run([
        FFMPEG_PATH, "-hide_banner", "-list_devices", "true",
        "-f", "dshow", "-i", "dummy",
    ])
    import re
    device_re = re.compile(r'\[(?:dshow|in#\d+).*?\]\s+"(.+?)"\s+\((video|audio)\)', re.IGNORECASE)
    audio_devices_list = [
        m.group(1).strip()
        for m in device_re.finditer(out)
        if m.group(2).lower() == "audio"
    ]
    section = _section(f"AUDIO DEVICES ({len(audio_devices_list)} found)")
    for name in audio_devices_list:
        section += f"  • {name}\n"
    return section


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    parts = [
        system_info(),
        ffmpeg_version(),
        ffmpeg_hwaccels(),
        ffmpeg_encoders(),
        verify_hardware_encoders(),
        camera_modes(),
        audio_devices(),
    ]

    full = "\n".join(parts)
    _save(OUTPUT_FILE, full)
    print(f"Saved to {OUTPUT_FILE}")
    print(f"Logs saved in {LOG_DIR}/")


if __name__ == "__main__":
    main()