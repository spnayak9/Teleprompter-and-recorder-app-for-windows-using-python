from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from teleprompter_app.config_manager import RecorderSettings
from teleprompter_app.system_profile import CameraProfile

log = logging.getLogger(__name__)


class FFmpegRecorderWorker(QObject):
    started = Signal()
    stopped = Signal(int)
    error = Signal(str)

    def __init__(
        self,
        ffmpeg_path: str,
        settings: RecorderSettings,
        camera: CameraProfile,
        output_path: Path,
    ) -> None:
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self.settings = settings
        self.camera = camera
        self.output_path = output_path
        self.process: subprocess.Popen | None = None
        self._stop_requested = False

    def _build_command(self) -> list[str]:
        # Parse resolution
        res = self.settings.resolution or "640x480"
        if "x" in res:
            width, height = res.split("x", 1)
        else:
            width, height = "640", "480"

        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-y",
            "-f",
            "dshow",
            "-rtbufsize",
            str(self.settings.rtbufsize),
            "-thread_queue_size",
            str(self.settings.thread_queue_size),
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(self.settings.fps),
            "-pixel_format",
            self.settings.pixel_format,
            "-i",
            f"video={self.camera.ffmpeg_name}",
        ]

        if self.settings.audio_device:
            cmd.extend(
                [
                    "-f",
                    "dshow",
                    "-thread_queue_size",
                    str(self.settings.thread_queue_size),
                    "-i",
                    f"audio={self.settings.audio_device}",
                ]
            )

        cmd.extend(["-map", "0:v:0"])

        if self.settings.audio_device:
            cmd.extend(["-map", "1:a:0"])

        cmd.extend(["-c:v", self.settings.video_codec])

        if self.settings.lossless:
            if self.settings.video_codec in {"ffv1"}:
                cmd.extend(["-level", "3"])
            elif self.settings.video_codec in {"libx264", "libx265"}:
                cmd.extend(["-crf", "0", "-preset", "ultrafast"])

        if self.settings.audio_device:
            cmd.extend(["-c:a", self.settings.audio_codec])

        if self.settings.extra_ffmpeg_args.strip():
            cmd.extend(self.settings.extra_ffmpeg_args.strip().split())

        cmd.append(str(self.output_path))

        return cmd

    @Slot()
    def run(self) -> None:
        try:
            cmd = self._build_command()
            log.info("Starting FFmpeg recording: %s", " ".join(cmd))

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )

            self.started.emit()

            assert self.process.stderr is not None
            for line in self.process.stderr:
                if line:
                    log.debug("ffmpeg: %s", line.rstrip())

                if self._stop_requested:
                    break

            return_code = self.process.wait()
            self.stopped.emit(return_code)

        except Exception as exc:
            log.exception("Recording failed")
            self.error.emit(str(exc))

    @Slot()
    def stop(self) -> None:
        self._stop_requested = True

        if self.process is None:
            return

        if self.process.poll() is not None:
            return

        try:
            if self.process.stdin:
                self.process.stdin.write("q\n")
                self.process.stdin.flush()
            self.process.wait(timeout=8)
        except Exception:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()


class RecordingController(QObject):
    started = Signal()
    stopped = Signal(int)
    error = Signal(str)

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self._thread: QThread | None = None
        self._worker: FFmpegRecorderWorker | None = None

    def start(
        self,
        settings: RecorderSettings,
        camera: CameraProfile,
        output_path: Path,
    ) -> None:
        if self._thread is not None:
            raise RuntimeError("Recording already running")

        self._thread = QThread()
        self._worker = FFmpegRecorderWorker(
            ffmpeg_path=self.ffmpeg_path,
            settings=settings,
            camera=camera,
            output_path=output_path,
        )

        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.started.connect(self.started)
        self._worker.stopped.connect(self.stopped)
        self._worker.error.connect(self.error)
        self._worker.stopped.connect(self._thread.quit)
        self._worker.stopped.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup)

        self._thread.start()

    def stop(self) -> None:
        if self._worker:
            self._worker.stop()

    def _cleanup(self) -> None:
        self._worker = None
        self._thread = None
