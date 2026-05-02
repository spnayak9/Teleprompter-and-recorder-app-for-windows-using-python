"""Project folder and timestamped recording file management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from teleprompter_app.recording.audio_config import RecordingConfig


@dataclass(frozen=True, slots=True)
class RecordingFiles:
    """Concrete paths for one recording session."""

    project_root: Path
    audio_dir: Path
    subtitles_dir: Path
    timestamp: str
    wav_path: Path | None
    flac_path: Path | None
    srt_path: Path
    transcript_path: Path


class RecordingFileManager:
    """Create the required project folder structure and session filenames."""

    def prepare_session(self, project_root: Path, config: RecordingConfig) -> RecordingFiles:
        project_root = project_root.expanduser().resolve()
        audio_dir = project_root / "audio"
        subtitles_dir = project_root / "subtitles"
        audio_dir.mkdir(parents=True, exist_ok=True)
        subtitles_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"recording_{timestamp}"

        return RecordingFiles(
            project_root=project_root,
            audio_dir=audio_dir,
            subtitles_dir=subtitles_dir,
            timestamp=timestamp,
            wav_path=audio_dir / f"{base_name}.wav" if config.wants_wav else None,
            flac_path=audio_dir / f"{base_name}.flac" if config.wants_flac else None,
            srt_path=subtitles_dir / f"{base_name}.srt",
            transcript_path=subtitles_dir / f"{base_name}.txt",
        )
