from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from teleprompter_app.config_manager import RecorderSettings
from teleprompter_app.recording.ffmpeg_commands import (
    build_audio_command,
    build_video_command,
)
from teleprompter_app.recording.ffmpeg_process import FFmpegProcessController
from teleprompter_app.recording.session_paths import RecordingSessionPaths
from teleprompter_app.system_profile import CameraProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecordingModeSpec:
    video: bool
    audio: bool
    srt: bool


MODE_MAP: dict[str, RecordingModeSpec] = {
    "srt only": RecordingModeSpec(video=False, audio=False, srt=True),
    "audio only": RecordingModeSpec(video=False, audio=True, srt=False),
    "video only": RecordingModeSpec(video=True, audio=False, srt=False),
    "audio + srt": RecordingModeSpec(video=False, audio=True, srt=True),
    "video + srt": RecordingModeSpec(video=True, audio=False, srt=True),
    "audio + video": RecordingModeSpec(video=True, audio=True, srt=False),
    "audio + video + srt": RecordingModeSpec(video=True, audio=True, srt=True),
}


class RecordingSessionController(QObject):
    started = Signal(object)
    stopped = Signal()
    error = Signal(str)

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self.video_process: FFmpegProcessController | None = None
        self.audio_process: FFmpegProcessController | None = None
        self.paths: RecordingSessionPaths | None = None
        self.mode_spec: RecordingModeSpec | None = None
        self._running_processes = 0

    def start(
        self,
        settings: RecorderSettings,
        camera: CameraProfile | None,
        paths: RecordingSessionPaths,
    ) -> RecordingModeSpec:
        mode_key = (settings.recording_mode or "").strip().lower()
        spec = MODE_MAP.get(mode_key)

        if spec is None:
            raise RuntimeError(f"Unsupported recording mode: {settings.recording_mode}")

        if spec.video and camera is None:
            raise RuntimeError("Video recording requested but no camera is selected")

        self.paths = paths
        self.mode_spec = spec
        self._running_processes = 0

        if spec.video:
            assert camera is not None
            video_cmd = build_video_command(
                self.ffmpeg_path,
                settings,
                camera,
                paths.video_path,
            )
            self.video_process = FFmpegProcessController(video_cmd)
            self.video_process.error.connect(self.error)
            self.video_process.stopped.connect(self._on_process_stopped)
            self._running_processes += 1
            self.video_process.start()

        if spec.audio:
            audio_cmd = build_audio_command(
                self.ffmpeg_path,
                settings,
                paths.audio_path,
            )
            self.audio_process = FFmpegProcessController(audio_cmd)
            self.audio_process.error.connect(self.error)
            self.audio_process.stopped.connect(self._on_process_stopped)
            self._running_processes += 1
            self.audio_process.start()

        self.started.emit(spec)

        return spec

    def stop(self) -> None:
        if self.video_process:
            self.video_process.stop()

        if self.audio_process:
            self.audio_process.stop()

        if not self.video_process and not self.audio_process:
            self.stopped.emit()

    def _on_process_stopped(self, _code: int) -> None:
        self._running_processes -= 1

        if self._running_processes <= 0:
            self.video_process = None
            self.audio_process = None
            self.stopped.emit()
