from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RecordingSessionPaths:
    session_id: int
    root: Path
    video_path: Path
    audio_path: Path
    subtitle_path: Path


VIDEO_EXTENSIONS = {"mkv", "mp4", "avi", "mov", "webm"}
AUDIO_EXTENSIONS = {"flac", "mp3", "wav", "m4a", "aac", "opus"}


def sanitize_video_ext(ext: str | None) -> str:
    value = (ext or "").strip().lower().lstrip(".")
    if value in VIDEO_EXTENSIONS:
        return value
    return "mkv"


def sanitize_audio_ext(ext: str | None) -> str:
    value = (ext or "").strip().lower().lstrip(".")
    if value in AUDIO_EXTENSIONS:
        return value
    return "flac"


def _next_session_id(root: Path) -> int:
    video_dir = root / "video"
    audio_dir = root / "audio"
    subtitles_dir = root / "subtitles"

    used: set[int] = set()

    for folder in (video_dir, audio_dir, subtitles_dir):
        if not folder.exists():
            continue

        for path in folder.iterdir():
            if not path.is_file():
                continue
            
            # Extract digits from the start of the filename (handles '27.word.srt', '27.mkv', etc)
            import re
            match = re.match(r"^(\d+)", path.stem)
            if match:
                used.add(int(match.group(1)))

    n = 1
    while n in used:
        n += 1

    return n


def create_session_paths(
    output_root: str | Path,
    video_ext: str,
    audio_ext: str,
) -> RecordingSessionPaths:
    root = Path(output_root).expanduser().resolve()

    video_dir = root / "video"
    audio_dir = root / "audio"
    subtitles_dir = root / "subtitles"

    video_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    subtitles_dir.mkdir(parents=True, exist_ok=True)

    session_id = _next_session_id(root)

    video_ext = sanitize_video_ext(video_ext)
    audio_ext = sanitize_audio_ext(audio_ext)

    return RecordingSessionPaths(
        session_id=session_id,
        root=root,
        video_path=video_dir / f"{session_id}.{video_ext}",
        audio_path=audio_dir / f"{session_id}.{audio_ext}",
        subtitle_path=subtitles_dir / f"{session_id}.srt",
    )
