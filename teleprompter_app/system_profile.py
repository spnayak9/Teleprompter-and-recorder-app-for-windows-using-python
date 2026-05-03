"""Parse and represent system diagnostics collected by the project's
diagnostics/profile script.

This module exposes a reusable `SystemProfile` dataclass and helpers to load
and parse the text output created by `profile.py` / `diagnostics.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re
from typing import List, Dict, Optional, Set


@dataclass(slots=True)
class CPUInfo:
    physical_cores: int
    logical_cores: int
    max_freq_mhz: Optional[float]


@dataclass(slots=True)
class GPUInfo:
    name: str
    adapter_ram: Optional[int]
    driver_version: Optional[str]
    hw_accels: List[str]


@dataclass(slots=True)
class RAMInfo:
    total_gb: float
    available_gb: float


@dataclass(slots=True)
class StorageInfo:
    device: str
    fstype: Optional[str]
    total_gb: Optional[float]
    free_gb: Optional[float]


@dataclass(slots=True)
class CameraMode:
    width: int
    height: int
    max_fps: float
    formats: Set[str]


@dataclass(slots=True)
class CameraDevice:
    name: str
    modes: List[CameraMode]


@dataclass(slots=True)
class AudioDevice:
    index: int
    name: str
    channels: int
    default_samplerate: Optional[float]
    supported_samplerates: List[int]


@dataclass(slots=True)
class SystemProfile:
    cpu: Optional[CPUInfo]
    gpus: List[GPUInfo]
    ram: Optional[RAMInfo]
    storage: List[StorageInfo]
    cameras: List[CameraDevice]
    audio_devices: List[AudioDevice]
    ffmpeg_hw_accels: List[str]

    def to_dict(self) -> Dict:
        return asdict(self)


_SECTION_RE = re.compile(r"^={10,}\n(?P<title>.+?)\n={10,}$(?P<body>.*?)(?=\n={10,}|\Z)", re.M | re.S)


def _split_sections(text: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    for m in _SECTION_RE.finditer(text):
        title = m.group("title").strip()
        body = m.group("body").strip()
        sections[title] = body
    return sections


def parse_profile_text(text: str) -> SystemProfile:
    sections = _split_sections(text)

    # CPU
    cpu = None
    cpu_body = sections.get("SYSTEM INFORMATION", "") + "\n" + sections.get("CPU INFORMATION", "")
    phys = re.search(r"Physical cores:\s*(\d+)", cpu_body)
    logic = re.search(r"Logical cores:\s*(\d+)", cpu_body)
    freq = re.search(r"Max Frequency:\s*([0-9.]+)\s*MHz", cpu_body)
    if phys or logic or freq:
        cpu = CPUInfo(
            physical_cores=int(phys.group(1)) if phys else 0,
            logical_cores=int(logic.group(1)) if logic else 0,
            max_freq_mhz=float(freq.group(1)) if freq else None,
        )

    # GPU
    gpus: List[GPUInfo] = []
    gpu_body = sections.get("GPU INFORMATION (WMI)", "")
    if gpu_body:
        for line in gpu_body.splitlines():
            line = line.strip()
            if not line:
                continue
            # Attempt: AdapterRAM  DriverVersion  Name
            m = re.match(r"(?P<ram>\d+)\s+(?P<driver>[\d\.]+)\s+(?P<name>.+)", line)
            if m:
                ram = int(m.group("ram"))
                driver = m.group("driver").strip()
                name = m.group("name").strip()
                gpus.append(GPUInfo(name=name, adapter_ram=ram, driver_version=driver, hw_accels=[]))
            else:
                # Fallback: take entire line as name
                gpus.append(GPUInfo(name=line, adapter_ram=None, driver_version=None, hw_accels=[]))

    # FFMPEG hardware accel list
    ffm = sections.get("FFMPEG INFORMATION", "")
    hw_accels: List[str] = []
    if ffm:
        # look for explicit hardware acceleration list or 'Hardware Acceleration:' section
        m = re.search(r"Hardware Acceleration:\s*(.*?)\n\n", ffm, re.S)
        if m:
            block = m.group(1)
            # split by non-word chars
            hw_accels = [s.strip() for s in re.split(r"[\s,]+", block) if s.strip()]
        else:
            # fallback: search for 'Hardware acceleration methods:' and following lines
            m2 = re.search(r"Hardware acceleration methods:\s*(.*?)\n\n", ffm, re.S)
            if m2:
                block = m2.group(1)
                hw_accels = [s.strip() for s in re.split(r"[\s,]+", block) if s.strip()]

    # RAM
    ram = None
    ram_body = sections.get("RAM INFORMATION", "")
    if ram_body:
        total = re.search(r"Total:\s*([0-9.]+)\s*GB", ram_body)
        avail = re.search(r"Available:\s*([0-9.]+)\s*GB", ram_body)
        if total:
            ram = RAMInfo(total_gb=float(total.group(1)), available_gb=float(avail.group(1)) if avail else 0.0)

    # Storage
    storage: List[StorageInfo] = []
    stor_body = sections.get("STORAGE INFORMATION", "")
    if stor_body:
        lines = stor_body.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            # device header like: C:\ (NTFS)
            m = re.match(r"(?P<dev>.+)\s*\((?P<fstype>.+)\)", line)
            if m:
                dev = m.group("dev").strip()
                fstype = m.group("fstype").strip()
                total = None
                free = None
                # look next couple lines for total/free
                j = i + 1
                while j < len(lines) and lines[j].startswith("  "):
                    tline = lines[j].strip()
                    t = re.match(r"Total:\s*([0-9.]+)\s*GB", tline)
                    f = re.match(r"Free:\s*([0-9.]+)\s*GB", tline)
                    if t:
                        total = float(t.group(1))
                    if f:
                        free = float(f.group(1))
                    j += 1
                storage.append(StorageInfo(device=dev, fstype=fstype, total_gb=total, free_gb=free))
                i = j
                continue
            i += 1

    # Camera devices
    cameras: List[CameraDevice] = []
    cam_body = sections.get("CAMERA INFORMATION (DIRECTSHOW)", "")
    if cam_body:
        cur_name = None
        cur_modes: Dict[tuple[int, int], CameraMode] = {}
        for line in cam_body.splitlines():
            line = line.rstrip()
            m_name = re.match(r"Camera:\s*(.+)", line)
            if m_name:
                if cur_name:
                    cameras.append(CameraDevice(name=cur_name, modes=list(cur_modes.values())))
                cur_name = m_name.group(1).strip()
                cur_modes = {}
                continue
            # parse mode lines like:  1920x1080 @ 60.0002 FPS [vcodec=mjpeg]
            m_mode = re.search(r"(\d+)x(\d+)\s*@\s*([0-9.]+)\s*FPS\s*\[(.*?)\]", line)
            if m_mode and cur_name:
                w = int(m_mode.group(1))
                h = int(m_mode.group(2))
                fps = float(m_mode.group(3))
                fmt = m_mode.group(4).strip()
                key = (w, h)
                if key not in cur_modes:
                    cur_modes[key] = CameraMode(width=w, height=h, max_fps=fps, formats={fmt})
                else:
                    cur_modes[key].formats.add(fmt)
                    if fps > cur_modes[key].max_fps:
                        cur_modes[key].max_fps = fps
        if cur_name:
            cameras.append(CameraDevice(name=cur_name, modes=list(cur_modes.values())))

    # Audio devices
    audio_devices: List[AudioDevice] = []
    aud_body = sections.get("AUDIO INPUT DEVICES", "")
    if aud_body:
        cur_index = None
        cur_name = None
        cur_channels = 0
        cur_default_sr = None
        supported: List[int] = []
        for line in aud_body.splitlines():
            line = line.rstrip()
            m_dev = re.match(r"Device\s*(?P<idx>\d+):\s*(?P<name>.+)", line)
            if m_dev:
                if cur_name is not None:
                    audio_devices.append(AudioDevice(index=cur_index, name=cur_name, channels=cur_channels, default_samplerate=cur_default_sr, supported_samplerates=supported))
                cur_index = int(m_dev.group("idx"))
                cur_name = m_dev.group("name").strip()
                cur_channels = 0
                cur_default_sr = None
                supported = []
                continue
            m_chan = re.search(r"Channels:\s*(\d+)", line)
            if m_chan:
                cur_channels = int(m_chan.group(1))
                continue
            m_def = re.search(r"Default SR:\s*([0-9.]+)", line)
            if m_def:
                cur_default_sr = float(m_def.group(1))
                continue
            m_sr = re.search(r"(\d+)\s*Hz:\s*(YES|NO)", line)
            if m_sr:
                rate = int(m_sr.group(1))
                ok = m_sr.group(2) == "YES"
                if ok:
                    supported.append(rate)
        if cur_name is not None:
            audio_devices.append(AudioDevice(index=cur_index, name=cur_name, channels=cur_channels, default_samplerate=cur_default_sr, supported_samplerates=supported))

    return SystemProfile(cpu=cpu, gpus=gpus, ram=ram, storage=storage, cameras=cameras, audio_devices=audio_devices, ffmpeg_hw_accels=hw_accels)


def load_profile_file(path: Path) -> SystemProfile:
    text = path.read_text(encoding="utf-8")
    return parse_profile_text(text)


__all__ = [
    "SystemProfile",
    "load_profile_file",
    "parse_profile_text",
    "CPUInfo",
    "GPUInfo",
    "RAMInfo",
    "StorageInfo",
    "CameraDevice",
    "CameraMode",
    "AudioDevice",
]
