from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from teleprompter_app.speech.recognizer import RecognitionResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SubtitleSegment:
    start: float
    end: float
    text: str


class SubtitleGenerator:
    """Collects recognition results and writes them to an SRT file."""

    def __init__(self, srt_path: Path) -> None:
        self.srt_path = srt_path
        self.segments: list[SubtitleSegment] = []
        self._current: SubtitleSegment | None = None
        self._last_word_end = 0.0
        self._start_time: float | None = None

    def start(self) -> None:
        import time
        self._start_time = time.monotonic()
        self.segments = []
        self._current = None
        self._last_word_end = 0.0
        logger.info("Subtitle writer started: %s", self.srt_path)

    def add_result(self, result: RecognitionResult) -> None:
        if self._start_time is None:
            return

        import time
        now = time.monotonic() - self._start_time

        if result.words:
            for word in result.words:
                text = word.word.strip()
                if not text:
                    continue
                
                # If word has timestamps from recognizer, use them (relative to recognition start)
                # But here we just want recording-relative.
                # Simplest is to use current elapsed time for the segment end.
                start = max(0.0, now - 0.5) # estimate
                end = now
                self._add_word(text, start, end)
        elif result.text.strip():
            self._add_segment(max(0.0, now - 1.5), now, result.text.strip())

    def stop(self) -> None:
        if self._current is not None:
            self.segments.append(self._current)
            self._current = None

        try:
            self.srt_path.parent.mkdir(parents=True, exist_ok=True)
            self.srt_path.write_text(self.to_srt(), encoding="utf-8")
            logger.info("Subtitle writer stopped. Saved to %s", self.srt_path)
        except Exception as exc:
            logger.error("Failed to save subtitles: %s", exc)
        
        self._start_time = None

    def _add_word(self, word: str, start: float, end: float) -> None:
        gap = start - self._last_word_end
        if self._current is None or gap > 0.85 or len(self._current.text.split()) >= 12:
            if self._current is not None:
                self.segments.append(self._current)
            self._current = SubtitleSegment(start=max(0.0, start), end=end, text=word)
        else:
            self._current.text = f"{self._current.text} {word}"
            self._current.end = max(self._current.end, end)

        self._last_word_end = max(self._last_word_end, end)

    def _add_segment(self, start: float, end: float, text: str) -> None:
        if self._current is not None:
            self.segments.append(self._current)
            self._current = None
        self.segments.append(SubtitleSegment(start=start, end=end, text=text))
        self._last_word_end = max(self._last_word_end, end)

    def to_srt(self) -> str:
        lines: list[str] = []
        for index, segment in enumerate(self.segments, start=1):
            lines.extend(
                [
                    str(index),
                    f"{self._format_srt_time(segment.start)} --> {self._format_srt_time(segment.end)}",
                    segment.text,
                    "",
                ]
            )
        return "\n".join(lines)

    def _format_srt_time(self, seconds: float) -> str:
        milliseconds = int(round(max(0.0, seconds) * 1000))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"
