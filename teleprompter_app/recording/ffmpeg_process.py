from __future__ import annotations

import logging
import subprocess
import re
import time
from collections import deque

from PySide6.QtCore import QObject, QThread, Signal, Slot

logger = logging.getLogger(__name__)


def normalize_return_code(code: int | None) -> int:
    if code is None:
        return 0

    code = int(code)

    if code > 2_147_483_647:
        code -= 4_294_967_296

    return code


_PROGRESS_RE = re.compile(
    r"frame=\s*(?P<frame>\d+).*?"
    r"fps=\s*(?P<fps>[0-9.]+).*?"
    r"time=(?P<time>[0-9:.]+).*?"
    r"speed=\s*(?P<speed>[0-9.]+x)",
    re.IGNORECASE,
)


class FFmpegProcessWorker(QObject):
    started = Signal()
    stopped = Signal(int)
    error = Signal(str)
    performance_warning = Signal(str)

    def __init__(self, cmd: list[str], kind: str = "ffmpeg") -> None:
        super().__init__()
        self.cmd = cmd
        self.kind = kind
        self.process: subprocess.Popen | None = None
        self._stop_requested = False
        self._stderr_tail: deque[str] = deque(maxlen=80)
        self._last_frame = -1
        self._last_frame_change_time = time.time()
        self._last_log_time = 0
        self._low_speed_count = 0

    @Slot()
    def run(self) -> None:
        try:
            logger.info("Starting FFmpeg process: %s", " ".join(self.cmd))

            self.process = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0,
            )

            self.started.emit()

            assert self.process.stderr is not None

            for line in self.process.stderr:
                line = line.rstrip()
                if line:
                    self._stderr_tail.append(line)
                    self._parse_progress(line)
                    logger.debug("ffmpeg [%s]: %s", self.kind, line)

                if self._stop_requested:
                    break

            code = normalize_return_code(self.process.wait())

            if code != 0 and not self._stop_requested:
                tail = "\n".join(self._stderr_tail)
                logger.error("FFmpeg failed with code %s:\n%s", code, tail)
                self.error.emit(f"FFmpeg failed with code {code}\n{tail}")

            self.stopped.emit(code)

        except Exception as exc:
            logger.exception("FFmpeg worker [%s] crashed", self.kind)
            self.error.emit(str(exc))
            self.stopped.emit(-1)

    def _parse_progress(self, line: str) -> None:
        match = _PROGRESS_RE.search(line)
        if not match:
            return

        frame = int(match.group("frame"))
        fps = match.group("fps")
        time_val = match.group("time")
        speed = match.group("speed")

        now = time.time()

        # Update stall tracking
        if frame != self._last_frame:
            self._last_frame = frame
            self._last_frame_change_time = now
        elif self.kind == "video" and frame > 0:
            elapsed = now - self._last_frame_change_time
            if elapsed > 5:
                logger.warning(
                    "FFmpeg [%s] appears stalled: frame=%s for %.1fs",
                    self.kind, frame, elapsed
                )

        # Log periodically (every 3 seconds)
        if now - self._last_log_time > 3:
            logger.info(
                "ffmpeg progress [%s]: frame=%s fps=%s time=%s speed=%s",
                self.kind, frame, fps, time_val, speed
            )
            self._last_log_time = now
            
            # Check for low performance (speed < 0.90x)
            try:
                speed_num = float(speed.replace("x", ""))
                if speed_num < 0.90:
                    self._low_speed_count += 1
                    if self._low_speed_count >= 2: # At least 6 seconds of low performance
                        msg = (f"Recording speed is low ({speed_num}x). "
                               "Video may stutter. Use Hardware Encoding or Stream Copy.")
                        self.performance_warning.emit(msg)
                else:
                    self._low_speed_count = 0
            except (ValueError, TypeError):
                pass

    @Slot()
    def stop(self) -> None:
        self._stop_requested = True

        if self.process is None or self.process.poll() is not None:
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


class FFmpegProcessController(QObject):
    started = Signal()
    stopped = Signal(int)
    error = Signal(str)
    performance_warning = Signal(str)

    def __init__(self, cmd: list[str], kind: str = "ffmpeg") -> None:
        super().__init__()
        self.cmd = cmd
        self.kind = kind
        self.thread: QThread | None = None
        self.worker: FFmpegProcessWorker | None = None

    def start(self) -> None:
        if self.thread is not None:
            raise RuntimeError("FFmpeg process already running")

        self.thread = QThread()
        self.worker = FFmpegProcessWorker(self.cmd, kind=self.kind)

        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.started.connect(self.started)
        self.worker.stopped.connect(self.stopped)
        self.worker.error.connect(self.error)
        self.worker.performance_warning.connect(self.performance_warning)

        self.worker.stopped.connect(self.thread.quit)
        self.worker.stopped.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._cleanup)

        self.thread.start()

    def stop(self) -> None:
        if self.worker:
            self.worker.stop()

    def stop_and_wait(self, timeout_ms: int = 5000) -> None:
        if self.worker:
            self.worker.stop()

        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(timeout_ms)

        self._cleanup()

    def _cleanup(self) -> None:
        self.worker = None
        self.thread = None

    def is_running(self) -> bool:
        return self.thread is not None and self.thread.isRunning()
