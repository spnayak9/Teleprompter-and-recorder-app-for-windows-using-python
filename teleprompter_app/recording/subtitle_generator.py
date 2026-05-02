"""Subtitle and transcript generation from recognition results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from teleprompter_app.speech.recognizer import RecognitionResult


@dataclass(slots=True)
class SubtitleSegment:
    """One subtitle segment with recording-relative timestamps."""

    start: float
    end: float
    text: str


class SubtitleGenerator:
    """Collect recognized speech and write SRT/TXT files."""

    def __init__(self, srt_path: Path, transcript_path: Path) -> None:
        self.srt_path = srt_path
        self.transcript_path = transcript_path
        self.segments: list[SubtitleSegment] = []
        self._current: SubtitleSegment | None = None
        self._last_word_end = 0.0

    def add_result(
        self,
        result: RecognitionResult,
        recognition_to_recording_offset: float,
        fallback_elapsed: float,
    ) -> None:
        """Add recognized words without touching recorded audio.

        NOTE: This method remains for backward compatibility. Prefer the
        `add_token_match` API which maps script tokens (highlighted words)
        to recording-relative timestamps so SRT contains the original script
        text rather than raw recognizer output.
        """

        if result.words:
            for word in result.words:
                text = word.word.strip()
                if not text:
                    continue
                start = self._relative_time(word.start, recognition_to_recording_offset, fallback_elapsed)
                end = self._relative_time(word.end, recognition_to_recording_offset, fallback_elapsed)
                if end <= start:
                    end = max(start + 0.2, fallback_elapsed)
                self._add_word(text, start, end)
            return

        text = result.text.strip()
        if text:
            self._add_segment(max(0.0, fallback_elapsed - 1.5), max(0.2, fallback_elapsed), text)

    def add_token_match(self, token_index: int, token_text: str, start: float | None, end: float | None) -> None:
        """Add a matched script token tied to recording-relative timestamps.

        This records the script `token_text` with the given `start` and `end`
        (both are recording-relative seconds). The generator groups nearby
        tokens into subtitle segments so the resulting SRT maps the original
        script text to the times it was highlighted.
        """
        if not token_text:
            return

        start_ts = start if start is not None else 0.0
        end_ts = end if end is not None else max(start_ts + 0.05, 0.05)
        # Use same grouping rules as _add_word but preserve full token text
        self._add_word(token_text.strip(), start_ts, end_ts)

    def finish(self) -> None:
        if self._current is not None:
            self.segments.append(self._current)
            self._current = None

        self.srt_path.parent.mkdir(parents=True, exist_ok=True)
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        self.srt_path.write_text(self.to_srt(), encoding="utf-8")
        self.transcript_path.write_text(self.to_text(), encoding="utf-8")

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

    def to_text(self) -> str:
        return "\n".join(segment.text for segment in self.segments)

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

    def _relative_time(self, value: float | None, offset: float, fallback_elapsed: float) -> float:
        if value is None:
            return max(0.0, fallback_elapsed)
        return max(0.0, value + offset)

    def _format_srt_time(self, seconds: float) -> str:
        milliseconds = int(round(max(0.0, seconds) * 1000))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"
