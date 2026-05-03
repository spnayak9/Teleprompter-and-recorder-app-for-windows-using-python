import os
import platform
import psutil
import subprocess
import re
import sounddevice as sd
from datetime import datetime

OUTPUT_FILE = "full_system_profile.txt"

# ---------- CONFIG ----------
# If ffmpeg is not in PATH, set full path here (optional)
# Get-Command ffmpeg | Select-Object Source

FFMPEG_PATH = r"C:\Users\shakt\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"

# ---------- UTILS ----------
def write(f, text=""):
    f.write(str(text) + "\n")

def section(f, title):
    write(f, "\n" + "="*70)
    write(f, title)
    write(f, "="*70)

def run_cmd(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        return result.stdout + result.stderr
    except Exception as e:
        return f"ERROR: {e}"

# ---------- SYSTEM ----------
def system_info(f):
    section(f, "SYSTEM INFORMATION")
    write(f, f"OS: {platform.system()} {platform.release()}")
    write(f, f"Version: {platform.version()}")
    write(f, f"Architecture: {platform.machine()}")
    write(f, f"Processor: {platform.processor()}")

# ---------- CPU ----------
def cpu_info(f):
    section(f, "CPU INFORMATION")
    write(f, f"Physical cores: {psutil.cpu_count(logical=False)}")
    write(f, f"Logical cores: {psutil.cpu_count(logical=True)}")
    freq = psutil.cpu_freq()
    if freq:
        write(f, f"Max Frequency: {freq.max:.2f} MHz")

# ---------- RAM ----------
def ram_info(f):
    section(f, "RAM INFORMATION")
    ram = psutil.virtual_memory()
    write(f, f"Total: {ram.total / (1024**3):.2f} GB")
    write(f, f"Available: {ram.available / (1024**3):.2f} GB")

# ---------- STORAGE ----------
def disk_info(f):
    section(f, "STORAGE INFORMATION")
    for p in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(p.mountpoint)
            write(f, f"{p.device} ({p.fstype})")
            write(f, f"  Total: {usage.total/(1024**3):.2f} GB")
            write(f, f"  Free: {usage.free/(1024**3):.2f} GB")
        except:
            continue

# ---------- GPU (WMI) ----------
def gpu_info(f):
    section(f, "GPU INFORMATION (WMI)")
    output = run_cmd(["wmic", "path", "win32_VideoController", "get", "name,AdapterRAM,DriverVersion"])
    write(f, output.strip())

# ---------- FFMPEG ----------
def ffmpeg_info(f):
    section(f, "FFMPEG INFORMATION")

    version = run_cmd([FFMPEG_PATH, "-version"])
    if "ERROR" in version or not version.strip():
        write(f, "FFmpeg not found or not accessible")
        return False

    write(f, version.split("\n")[0])

    write(f, "\nHardware Acceleration:")
    write(f, run_cmd([FFMPEG_PATH, "-hwaccels"]))

    write(f, "\nEncoders (filtered):")
    enc = run_cmd([FFMPEG_PATH, "-encoders"])
    for codec in ["h264", "hevc", "mpeg4", "vp9"]:
        write(f, f"{codec}: {'YES' if codec in enc else 'NO'}")

    return True

# ---------- CAMERAS ----------
def get_cameras():
    output = run_cmd([FFMPEG_PATH, "-list_devices", "true", "-f", "dshow", "-i", "dummy"])
    lines = output.splitlines()

    cameras = []
    current = None

    for line in lines:
        name_match = re.search(r'"(.*?)"\s+\(video\)', line)
        if name_match:
            current = name_match.group(1)

        alt_match = re.search(r'Alternative name "(.*?)"', line)
        if alt_match and current:
            cameras.append((current, alt_match.group(1)))
            current = None

    return cameras

def parse_camera_modes(output):
    modes = {}

    for line in output.splitlines():
        fmt_match = re.search(r"(vcodec=\w+|pixel_format=\w+)", line)
        if not fmt_match:
            continue

        fmt = fmt_match.group(1)

        res_match = re.search(r"min s=(\d+)x(\d+)", line)
        if not res_match:
            continue

        w, h = map(int, res_match.groups())

        fps_match = re.search(r"max s=\d+x\d+ fps=(\d+\.?\d*)", line)
        if not fps_match:
            continue

        fps = float(fps_match.group(1))

        key = (w, h)
        if key not in modes or fps > modes[key][1]:
            modes[key] = (fmt, fps)

    return modes

def camera_info(f):
    section(f, "CAMERA INFORMATION (DIRECTSHOW)")

    cameras = get_cameras()

    if not cameras:
        write(f, "No cameras detected")
        return

    for name, device in cameras:
        write(f, f"\nCamera: {name}")

        output = run_cmd([
            FFMPEG_PATH,
            "-f", "dshow",
            "-list_options", "true",
            "-i", f"video={device}"
        ])

        modes = parse_camera_modes(output)

        if not modes:
            write(f, "  No modes detected")
            continue

        for (w, h), (fmt, fps) in sorted(modes.items(), key=lambda x: x[0][0]*x[0][1]):
            write(f, f"  {w}x{h} @ {fps} FPS [{fmt}]")

# ---------- AUDIO ----------
def audio_info(file):
    section(file, "AUDIO INPUT DEVICES")

    try:
        devices = sd.query_devices()
    except Exception as e:
        write(file, f"Error: {e}")
        return

    for i, device in enumerate(devices):
        if device['max_input_channels'] > 0:
            write(file, f"\nDevice {i}: {device['name']}")
            write(file, f"  Channels: {device['max_input_channels']}")
            write(file, f"  Default SR: {device['default_samplerate']}")

            for sr in [8000, 16000, 44100, 48000]:
                try:
                    sd.check_input_settings(device=i, samplerate=sr)
                    write(file, f"    {sr} Hz: YES")
                except:
                    write(file, f"    {sr} Hz: NO")

# ---------- MAIN ----------
def main():
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        write(f, "FULL SYSTEM DIAGNOSTICS REPORT")
        write(f, f"Generated: {datetime.now()}")

        system_info(f)
        cpu_info(f)
        ram_info(f)
        disk_info(f)
        gpu_info(f)

        ffmpeg_ok = ffmpeg_info(f)

        if ffmpeg_ok:
            camera_info(f)
        else:
            write(f, "\nCamera analysis skipped (FFmpeg required)")

        audio_info(f)

    print(f"\nSaved to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()