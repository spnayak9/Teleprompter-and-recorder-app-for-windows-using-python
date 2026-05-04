from __future__ import annotations

import logging
import time

import cv2
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

logger = logging.getLogger(__name__)


class PreviewWorker(QThread):
    frame_ready = Signal(QImage)
    fps_ready = Signal(float)
    error = Signal(str)

    def __init__(self, camera_index: int, width: int | None = None, height: int | None = None):
        super().__init__()
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self._running = False

    def run(self) -> None:
        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)

        try:
            if not cap.isOpened():
                self.error.emit(f"Could not open camera index {self.camera_index}")
                return

            if self.width and self.height:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

            self._running = True
            frame_count = 0
            started_at = time.time()

            logger.info("Starting preview worker for camera index %s", self.camera_index)

            while self._running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.01)
                    continue

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape

                image = QImage(
                    rgb.data,
                    w,
                    h,
                    ch * w,
                    QImage.Format.Format_RGB888,
                ).copy()

                self.frame_ready.emit(image)

                frame_count += 1
                elapsed = time.time() - started_at
                if elapsed >= 1.0:
                    self.fps_ready.emit(frame_count / elapsed)
                    frame_count = 0
                    started_at = time.time()

        except Exception as exc:
            logger.exception("Preview worker failed")
            self.error.emit(str(exc))
        finally:
            self._running = False
            cap.release()
            logger.info("Preview worker stopped and camera released")

    def request_stop(self) -> None:
        self._running = False


class PreviewController(QObject):
    frame_ready = Signal(QImage)
    fps_ready = Signal(float)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._worker: PreviewWorker | None = None

    def start(
        self,
        camera_index: int,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        self.stop(wait=True)

        worker = PreviewWorker(camera_index, width, height)
        worker.frame_ready.connect(self.frame_ready)
        worker.fps_ready.connect(self.fps_ready)
        worker.error.connect(self.error)
        worker.finished.connect(worker.deleteLater)

        self._worker = worker
        worker.start()

    def stop(self, wait: bool = True) -> None:
        worker = self._worker
        if worker is None:
            return

        worker.request_stop()

        if wait and worker.isRunning():
            worker.wait(3000)

        self._worker = None

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()


__all__ = ["PreviewWorker", "PreviewController"]
