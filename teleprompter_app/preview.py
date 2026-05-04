from __future__ import annotations

import logging
import time

import cv2
from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QImage

log = logging.getLogger(__name__)


class PreviewWorker(QObject):
    frame_ready = Signal(QImage)
    fps_ready = Signal(float)
    error = Signal(str)
    stopped = Signal()

    def __init__(self, camera_index: int, width: int | None = None, height: int | None = None) -> None:
        super().__init__()
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self._running = False

    @Slot()
    def run(self) -> None:
        log.info("Starting preview worker for camera index %s", self.camera_index)

        # Use DSHOW for consistency on Windows
        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)

        try:
            if not cap.isOpened():
                self.error.emit(f"Could not open camera index {self.camera_index}")
                return

            if self.width:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            if self.height:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

            self._running = True
            prev_time = time.time()

            while self._running:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.02)
                    continue

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                bytes_per_line = ch * w

                image = QImage(
                    rgb.data,
                    w,
                    h,
                    bytes_per_line,
                    QImage.Format.Format_RGB888,
                ).copy()

                self.frame_ready.emit(image)
                
                # FPS calculation
                curr_time = time.time()
                dt = curr_time - prev_time
                if dt > 0.5: # Update FPS every 0.5s
                    fps = 1.0 / (dt if dt > 0 else 0.033)
                    self.fps_ready.emit(fps)
                    prev_time = curr_time
                    
                time.sleep(0.001)

        except Exception as exc:
            log.exception("Preview worker failed")
            self.error.emit(str(exc))
        finally:
            cap.release()
            self._running = False
            log.info("Preview worker stopped and camera released")
            self.stopped.emit()

    @Slot()
    def stop(self) -> None:
        self._running = False


class PreviewController(QObject):
    frame_ready = Signal(QImage)
    fps_ready = Signal(float)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._thread: QThread | None = None
        self._worker: PreviewWorker | None = None

    def start(self, camera_index: int, width: int | None = None, height: int | None = None) -> None:
        self.stop(wait=True)

        self._thread = QThread()
        self._worker = PreviewWorker(camera_index, width, height)

        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.frame_ready.connect(self.frame_ready)
        self._worker.fps_ready.connect(self.fps_ready)
        self._worker.error.connect(self.error)
        self._worker.stopped.connect(self._thread.quit)
        self._worker.stopped.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def stop(self, wait: bool = True) -> None:
        if self._worker is not None:
            self._worker.stop()

        if self._thread is not None:
            self._thread.quit()
            if wait:
                self._thread.wait(3000)

        self._worker = None
        self._thread = None
