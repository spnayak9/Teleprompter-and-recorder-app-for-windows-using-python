from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from teleprompter_app.utils.config import AppSettings
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


def normalize_recording_mode(value: str) -> str:
    text = (value or "").strip().lower()
    text = text.replace("&", "+")
    text = text.replace(" and ", " + ")
    text = text.replace(" with ", " + ")
    text = " ".join(text.split())

    aliases = {
        "record only srt": "srt only",
        "srt only": "srt only",
        "only srt": "srt only",
        "audio only": "audio only",
        "record audio only": "audio only",
        "video only": "video only",
        "record video only": "video only",
        "audio + srt": "audio + srt",
        "audio srt": "audio + srt",
        "video + srt": "video + srt",
        "video srt": "video + srt",
        "audio + video only": "audio + video",
        "audio + video": "audio + video",
        "video + audio": "audio + video",
        "audio + video + srt": "audio + video + srt",
        "video + audio + srt": "audio + video + srt",
        "audio video srt": "audio + video + srt",
    }

    return aliases.get(text, text)


class RecordingSessionController(QObject):
    started = Signal(object)
    stopped = Signal()
    error = Signal(str)
    performance_warning = Signal(str)

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self.video_process: FFmpegProcessController | None = None
        self.audio_process: FFmpegProcessController | None = None
        self.mode_spec: RecordingModeSpec | None = None
        self._running_processes = 0
        self._stopped_emitted = False

    def start(
        self,
        settings: AppSettings,
        camera: CameraProfile | None,
        paths: RecordingSessionPaths,
    ) -> RecordingModeSpec:
        mode_key = normalize_recording_mode(settings.recording_mode)
        spec = MODE_MAP.get(mode_key)

        if spec is None:
            raise RuntimeError(f"Unsupported recording mode: {settings.recording_mode}")

        logger.info(
            "Recording mode resolved: raw=%r normalized=%r video=%s audio=%s srt=%s",
            settings.recording_mode,
            mode_key,
            spec.video,
            spec.audio,
            spec.srt,
        )

        if spec.video and camera is None:
            raise RuntimeError("Video recording requested but no camera is selected")

        self.paths = paths
        self.mode_spec = spec
        self._running_processes = 0
        self._stopped_emitted = False

        if spec.video:
            assert camera is not None
            video_cmd = build_video_command(
                self.ffmpeg_path,
                settings,
                camera,
                paths.video_path,
            )
            self.video_process = FFmpegProcessController(video_cmd, kind="video")
            self.video_process.error.connect(self._on_process_error)
            self.video_process.stopped.connect(self._on_process_stopped)
            self.video_process.performance_warning.connect(self.performance_warning)
            self._running_processes += 1
            self.video_process.start()

        if spec.audio:
            audio_cmd = build_audio_command(
                self.ffmpeg_path,
                settings,
                paths.audio_path,
            )
            self.audio_process = FFmpegProcessController(audio_cmd, kind="audio")
            self.audio_process.error.connect(self._on_process_error)
            self.audio_process.stopped.connect(self._on_process_stopped)
            self.audio_process.performance_warning.connect(self.performance_warning)
            self._running_processes += 1
            self.audio_process.start()

        self.started.emit(spec)

        return spec

    def stop(self) -> None:
        if self.video_process:
            self.video_process.stop_and_wait()

        if self.audio_process:
            self.audio_process.stop_and_wait()

        self.video_process = None
        self.audio_process = None
        self._running_processes = 0
        self._emit_stopped_once()

    def _on_process_stopped(self, _code: int) -> None:
        self._running_processes = max(0, self._running_processes - 1)

        if self._running_processes <= 0:
            self._emit_stopped_once()

    def _emit_stopped_once(self) -> None:
        if self._stopped_emitted:
            return
        self._stopped_emitted = True
        self.video_process = None
        self.audio_process = None
        self.stopped.emit()

    def _on_process_error(self, message: str) -> None:
        logger.error("Recording process failed: %s", message)

        if self.video_process:
            self.video_process.stop()

        if self.audio_process:
            self.audio_process.stop()

        self.error.emit(message)
