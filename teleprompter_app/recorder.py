"""FFmpeg-based recording engine.

Provides a flexible `FFmpegRecorder` that can record video and/or audio from
DirectShow devices on Windows using `ffmpeg` subprocesses. The implementation
exposes a simple configuration dataclass and a start/stop API. It also
monitors ffmpeg stderr to extract realtime FPS estimates.
"""
from __future__ import annotations

import subprocess
import threading
import time
import shutil
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RecorderConfig:
    ffmpeg_path: str = "ffmpeg"
    video_device: Optional[str] = None
    audio_device: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    pixel_format: Optional[str] = None
    video_codec: str = "ffv1"  # default lossless
    audio_codec: str = "flac"
    audio_sample_rate: Optional[int] = None
    audio_channels: Optional[int] = None
    output_container: str = "mkv"
    rtbufsize: str = "200M"
    thread_queue_size: int = 512
    extra_ffmpeg_args: List[str] = None


class FFmpegRecorder:
    """Control an ffmpeg subprocess to capture audio/video streams.

    The recorder attempts to build a DirectShow input command and run ffmpeg
    in a background thread. It monitors stderr to extract FPS estimates and
    to surface ffmpeg logs to the application logger.
    """

    def __init__(self, config: RecorderConfig, output_path: Path):
        self.config = config
        self.output_path = Path(output_path)
        self._proc: Optional[subprocess.Popen] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._latest_fps: float = 0.0
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _find_ffmpeg(self) -> str:
        path = self.config.ffmpeg_path or "ffmpeg"
        if shutil.which(path):
            return path
        # fallback: try plain ffmpeg
        if shutil.which("ffmpeg"):
            return "ffmpeg"
        raise RuntimeError("ffmpeg executable not found; configure `ffmpeg_path` in RecorderConfig")

    def _build_dshow_input(self) -> List[str]:
        # Compose DirectShow input string(s).
        video = self.config.video_device
        audio = self.config.audio_device
        # Support two input modes:
        # - DirectShow devices (Windows): video=<name> or video=<name>:audio=<name>
        # - Screen capture prefix 'screen:<title>' which uses gdigrab (Windows)
        if video and video.startswith("screen:"):
            # screen capture: 'screen:desktop' or 'screen:Window Title'
            title = video.split(":", 1)[1] or "desktop"
            args: List[str] = ["-f", "gdigrab"]
            # thread queue size is not applicable to gdigrab but keep option ordering
            args.extend(["-thread_queue_size", str(self.config.thread_queue_size)])
            if self.config.fps:
                args.extend(["-framerate", str(int(self.config.fps))])
            # use title= for window capture, or desktop for full screen
            if title.lower() == "desktop":
                args.extend(["-i", "desktop"])
            else:
                args.extend(["-i", f"title={title}"])
            # optionally attach audio input if requested (use default audio device name)
            if audio:
                # append audio input using dshow on Windows
                args.extend(["-f", "dshow", "-i", f"audio={audio}"])
            return args

        # default: DirectShow device(s)
        if video and audio:
            input_spec = f"video={video}:audio={audio}"
        elif video:
            input_spec = f"video={video}"
        elif audio:
            input_spec = f"audio={audio}"
        else:
            raise RuntimeError("No video or audio device configured for recording")

        args: List[str] = ["-f", "dshow"]
        # thread queue size before input helps prevent frame drops
        args.extend(["-thread_queue_size", str(self.config.thread_queue_size)])
        # optional framerate for capture devices
        if self.config.fps and video:
            args.extend(["-framerate", str(int(self.config.fps))])
        args.extend(["-i", input_spec])
        return args

    def _build_output_args(self) -> List[str]:
        args: List[str] = []
        # choose codecs and container
        vc = self.config.video_codec
        ac = self.config.audio_codec

        if self.config.video_device and vc:
            # allow copy for mjpeg if user selected an identity codec
            if vc == "copy":
                args.extend(["-c:v", "copy"])
            else:
                args.extend(["-c:v", vc])

        if self.config.audio_device and ac:
            if ac == "copy":
                args.extend(["-c:a", "copy"])
            else:
                args.extend(["-c:a", ac])

        if self.config.pixel_format:
            args.extend(["-pix_fmt", self.config.pixel_format])

        # tuning for robust capture
        args.extend(["-rtbufsize", self.config.rtbufsize, "-fflags", "+genpts"])

        # allow extra user flags
        if self.config.extra_ffmpeg_args:
            args.extend(self.config.extra_ffmpeg_args)

        return args

    def _build_command(self) -> List[str]:
        ffmpeg = self._find_ffmpeg()
        cmd: List[str] = [ffmpeg, "-y"]
        # set indexing to use wallclock timestamps
        cmd.extend(["-use_wallclock_as_timestamps", "1"])
        # input building
        cmd.extend(self._build_dshow_input())

        # output building
        cmd.extend(self._build_output_args())

        outfile = str(self.output_path)
        # ensure container selection
        cmd.append(outfile)
        return cmd

    def start(self) -> None:
        if self.is_running:
            raise RuntimeError("Recorder is already running")

        cmd = self._build_command()
        logger.info("Starting ffmpeg: %s", " ".join(cmd))

        # spawn subprocess and monitor stderr
        self._stop_event.clear()
        self._proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL)

        def _stderr_watcher(proc: subprocess.Popen):
            assert proc.stderr is not None
            for raw in iter(proc.stderr.readline, b""):
                if self._stop_event.is_set():
                    break
                try:
                    line = raw.decode("utf-8", errors="ignore").strip()
                except Exception:
                    continue
                if not line:
                    continue
                logger.debug("ffmpeg: %s", line)
                # parse fps from common ffmpeg progress lines
                m = re_search_fps(line)
                if m is not None:
                    with self._lock:
                        self._latest_fps = m

            # ensure process has exited
            try:
                proc.wait(timeout=1)
            except Exception:
                pass

        self._stderr_thread = threading.Thread(target=_stderr_watcher, args=(self._proc,), daemon=True)
        self._stderr_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if not self._proc:
            return
        self._stop_event.set()
        try:
            # try gentle termination
            self._proc.terminate()
            self._proc.wait(timeout=timeout)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        finally:
            self._proc = None

    def get_fps(self) -> float:
        with self._lock:
            return float(self._latest_fps or 0.0)


def re_search_fps(line: str) -> Optional[float]:
    # common ffmpeg progress contains 'fps=123.4' or 'frame= 123 fps=123.4'
    m = re.search(r"fps=\s*([0-9]+\.?[0-9]*)", line)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


__all__ = ["RecorderConfig", "FFmpegRecorder"]
