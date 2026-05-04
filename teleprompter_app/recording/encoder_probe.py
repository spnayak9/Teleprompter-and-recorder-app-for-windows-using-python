"""
encoder_probe.py — Hardware and software encoder discovery via ffmpeg -encoders.

Design decisions:
- Uses `ffmpeg -encoders` (NOT `-codecs` or `-hwaccels`) for actual encoder availability.
- Hardware encoders are discovered but NOT verified at startup (lazy verification).
- `verification_status` starts as "unknown" for hardware encoders, "usable" for software.
- Call `verify_encoder_usable()` at the point the user selects a hardware encoder.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Regex to parse a line like:
# " V..... h264_amf             AMD H.264/AVC VCE encoder"
_ENCODER_LINE_RE = re.compile(r"^\s*[A-Z.]{6}\s+(?P<name>[a-zA-Z0-9_]+)\s+")


# ---------------------------------------------------------------------------
# Known encoder catalog
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EncoderCatalogEntry:
    label: str
    kind: str          # "hardware" | "software"
    vendor: str        # "nvidia" | "amd" | "intel" | "software"
    codec_family: str  # "h264" | "hevc" | "av1" | "ffv1" | "mjpeg"
    lossless_capable: bool
    realtime_4k_recommended: bool


HARDWARE_ENCODER_CATALOG: dict[str, EncoderCatalogEntry] = {
    "h264_nvenc": EncoderCatalogEntry(
        label="NVIDIA H.264 NVENC", kind="hardware", vendor="nvidia",
        codec_family="h264", lossless_capable=True, realtime_4k_recommended=True,
    ),
    "hevc_nvenc": EncoderCatalogEntry(
        label="NVIDIA HEVC NVENC", kind="hardware", vendor="nvidia",
        codec_family="hevc", lossless_capable=False, realtime_4k_recommended=True,
    ),
    "av1_nvenc": EncoderCatalogEntry(
        label="NVIDIA AV1 NVENC", kind="hardware", vendor="nvidia",
        codec_family="av1", lossless_capable=False, realtime_4k_recommended=True,
    ),
    "h264_qsv": EncoderCatalogEntry(
        label="Intel H.264 Quick Sync", kind="hardware", vendor="intel",
        codec_family="h264", lossless_capable=False, realtime_4k_recommended=True,
    ),
    "hevc_qsv": EncoderCatalogEntry(
        label="Intel HEVC Quick Sync", kind="hardware", vendor="intel",
        codec_family="hevc", lossless_capable=False, realtime_4k_recommended=True,
    ),
    "av1_qsv": EncoderCatalogEntry(
        label="Intel AV1 Quick Sync", kind="hardware", vendor="intel",
        codec_family="av1", lossless_capable=False, realtime_4k_recommended=True,
    ),
    "h264_amf": EncoderCatalogEntry(
        label="AMD H.264 AMF", kind="hardware", vendor="amd",
        codec_family="h264", lossless_capable=False, realtime_4k_recommended=True,
    ),
    "hevc_amf": EncoderCatalogEntry(
        label="AMD HEVC AMF", kind="hardware", vendor="amd",
        codec_family="hevc", lossless_capable=False, realtime_4k_recommended=True,
    ),
    "av1_amf": EncoderCatalogEntry(
        label="AMD AV1 AMF", kind="hardware", vendor="amd",
        codec_family="av1", lossless_capable=False, realtime_4k_recommended=True,
    ),
}

SOFTWARE_ENCODER_CATALOG: dict[str, EncoderCatalogEntry] = {
    "libx264": EncoderCatalogEntry(
        label="H.264 Software (x264)", kind="software", vendor="software",
        codec_family="h264", lossless_capable=True, realtime_4k_recommended=False,
    ),
    "libx265": EncoderCatalogEntry(
        label="HEVC Software (x265)", kind="software", vendor="software",
        codec_family="hevc", lossless_capable=True, realtime_4k_recommended=False,
    ),
    "ffv1": EncoderCatalogEntry(
        label="FFV1 Lossless", kind="software", vendor="software",
        codec_family="ffv1", lossless_capable=True, realtime_4k_recommended=False,
    ),
    "mjpeg": EncoderCatalogEntry(
        label="MJPEG Software", kind="software", vendor="software",
        codec_family="mjpeg", lossless_capable=False, realtime_4k_recommended=False,
    ),
}


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------

def _run_ffmpeg(args: list[str], timeout: int = 20) -> str:
    try:
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
    except Exception as exc:
        logger.warning("ffmpeg probe failed %s: %s", args[:3], exc)
        return ""


def list_available_encoder_names(ffmpeg_path: str = "ffmpeg") -> set[str]:
    """
    Run `ffmpeg -hide_banner -encoders` and return the set of encoder names.
    This is the correct way to discover actual available encoders.
    """
    output = _run_ffmpeg([ffmpeg_path, "-hide_banner", "-encoders"])
    names: set[str] = set()
    for line in output.splitlines():
        m = _ENCODER_LINE_RE.match(line)
        if m:
            names.add(m.group("name"))
    logger.debug("FFmpeg encoders available (%d): %s", len(names), sorted(names))
    return names


def dump_encoders_to_log(ffmpeg_path: str = "ffmpeg") -> None:
    """Save full ffmpeg -encoders output to logs/ffmpeg_encoders.txt for diagnostics."""
    try:
        output = _run_ffmpeg([ffmpeg_path, "-hide_banner", "-encoders"])
        log_dir = Path.cwd() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "ffmpeg_encoders.txt").write_text(output, encoding="utf-8")
        logger.info("Saved ffmpeg encoder list to logs/ffmpeg_encoders.txt")
    except Exception:
        logger.exception("Could not dump encoder list")


def verify_encoder_usable(ffmpeg_path: str, encoder_name: str, timeout: int = 15) -> tuple[bool, str]:
    """
    Run a 1-second test encode to verify the encoder is actually functional.
    Returns (usable: bool, failure_reason: str).
    Called lazily when user selects a hardware encoder — NOT at startup.
    """
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-y",
        "-f", "lavfi",
        "-i", "testsrc2=s=1280x720:r=30",
        "-t", "1",
        "-c:v", encoder_name,
        "-f", "null",
        "-",
    ]
    logger.info("Verifying encoder %r with 1s test encode...", encoder_name)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if proc.returncode == 0:
            logger.info("Encoder %r verified usable.", encoder_name)
            return True, ""
        else:
            reason = (proc.stderr or "")[-1500:]
            logger.warning("Encoder %r failed verification (code %d):\n%s", encoder_name, proc.returncode, reason)
            return False, reason
    except subprocess.TimeoutExpired:
        reason = f"Verification timed out after {timeout}s"
        logger.warning("Encoder %r: %s", encoder_name, reason)
        return False, reason
    except Exception as exc:
        reason = str(exc)
        logger.warning("Encoder %r: %s", encoder_name, reason)
        return False, reason


# ---------------------------------------------------------------------------
# High-level probe
# ---------------------------------------------------------------------------

def probe_detected_encoders(ffmpeg_path: str = "ffmpeg") -> list[dict]:
    """
    Detect hardware and software encoders available in this FFmpeg build.
    Hardware encoders are returned with verification_status='unknown' (lazy verify).
    Software encoders are returned with verification_status='usable'.

    Returns a list of dicts suitable for constructing VideoEncoderProfile objects.
    """
    available = list_available_encoder_names(ffmpeg_path)
    results: list[dict] = []

    # Priority order for hardware encoders: NVENC > QSV > AMF
    for name, entry in HARDWARE_ENCODER_CATALOG.items():
        if name in available:
            results.append({
                "name": name,
                "label": entry.label,
                "kind": entry.kind,
                "vendor": entry.vendor,
                "codec_family": entry.codec_family,
                "lossless_capable": entry.lossless_capable,
                "realtime_4k_recommended": entry.realtime_4k_recommended,
                "verification_status": "unknown",  # lazy — not verified at startup
                "failure_reason": "",
            })
            logger.info("Hardware encoder detected (unverified): %s (%s)", name, entry.label)

    for name, entry in SOFTWARE_ENCODER_CATALOG.items():
        if name in available:
            results.append({
                "name": name,
                "label": entry.label,
                "kind": entry.kind,
                "vendor": entry.vendor,
                "codec_family": entry.codec_family,
                "lossless_capable": entry.lossless_capable,
                "realtime_4k_recommended": entry.realtime_4k_recommended,
                "verification_status": "usable",  # software encoders always usable
                "failure_reason": "",
            })
            logger.info("Software encoder detected: %s (%s)", name, entry.label)

    hw_count = sum(1 for r in results if r["kind"] == "hardware")
    sw_count = sum(1 for r in results if r["kind"] == "software")
    logger.info(
        "Encoder probe complete: %d hardware detected (unverified), %d software",
        hw_count, sw_count,
    )
    return results
