"""Project folder and timestamped recording file management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from teleprompter_app.recording.audio_config import RecordingConfig
import re


@dataclass(frozen=True, slots=True)
class RecordingFiles:
    """Concrete paths for one recording session."""

    project_root: Path
    audio_dir: Path
    subtitles_dir: Path
    session_name: str
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
        # Determine next numeric session index by scanning existing files like "1.srt", "2.wav", etc.
        pattern = re.compile(r"^(?P<num>\d+)\.(wav|flac|srt|txt)$", re.IGNORECASE)
        max_num = 0
        for p in list(audio_dir.iterdir()) + list(subtitles_dir.iterdir()):
            if not p.is_file():
                continue
            m = pattern.match(p.name)
            if not m:
                continue
            try:
                num = int(m.group("num"))
            except Exception:
                continue
            if num > max_num:
                max_num = num

        next_num = max_num + 1
        base_name = str(next_num)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        return RecordingFiles(
            project_root=project_root,
            audio_dir=audio_dir,
            subtitles_dir=subtitles_dir,
            session_name=base_name,
            timestamp=timestamp,
            wav_path=audio_dir / f"{base_name}.wav" if config.wants_wav else None,
            flac_path=audio_dir / f"{base_name}.flac" if config.wants_flac else None,
            srt_path=subtitles_dir / f"{base_name}.srt",
            transcript_path=subtitles_dir / f"{base_name}.txt",
        )
