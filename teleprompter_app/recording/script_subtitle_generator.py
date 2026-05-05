from __future__ import annotations

import logging
import re
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class SubtitleBlock:
    index: int
    start: float
    end: float
    text: str

    def to_srt_block(self) -> str:
        return (
            f"{self.index}\n"
            f"{format_srt_time(self.start)} --> {format_srt_time(self.end)}\n"
            f"{self.text}\n"
        )

def format_srt_time(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

class ScriptSubtitleGenerator:
    """
    Generates deterministic subtitles from a teleprompter script.
    Calculates timing based on Words Per Minute (WPM).
    """

    def __init__(self, script_text: str, wpm: int = 150) -> None:
        self.script_text = script_text.strip()
        self.wpm = wpm
        self.seconds_per_word = 60.0 / wpm
        
        # Phrases (v1): Lines from the script
        self.phrases: list[str] = [
            line.strip() for line in self.script_text.splitlines() 
            if line.strip()
        ]
        
        # Words (v2): All individual words
        self.words: list[str] = []
        for phrase in self.phrases:
            self.words.extend(phrase.split())

    def generate_v1_phrase_srt(self, max_duration: float | None = None) -> str:
        """v1: Grouped words based on line breaks in script."""
        blocks: list[SubtitleBlock] = []
        current_time = 0.0
        
        for i, phrase in enumerate(self.phrases, start=1):
            word_count = len(phrase.split())
            duration = word_count * self.seconds_per_word
            
            end_time = current_time + duration
            
            # Clip to max_duration if provided
            if max_duration is not None and current_time >= max_duration:
                break
            
            display_end = end_time
            if max_duration is not None and end_time > max_duration:
                display_end = max_duration

            blocks.append(SubtitleBlock(
                index=i,
                start=current_time,
                end=display_end,
                text=phrase
            ))
            current_time += duration
            
        return "\n".join(b.to_srt_block() for b in blocks)

    def generate_v2_word_srt(self, max_duration: float | None = None) -> str:
        """v2: Single-word subtitles."""
        blocks: list[SubtitleBlock] = []
        current_time = 0.0
        
        for i, word in enumerate(self.words, start=1):
            duration = self.seconds_per_word
            end_time = current_time + duration

            if max_duration is not None and current_time >= max_duration:
                break

            display_end = end_time
            if max_duration is not None and end_time > max_duration:
                display_end = max_duration

            blocks.append(SubtitleBlock(
                index=i,
                start=current_time,
                end=display_end,
                text=word
            ))
            current_time += duration
            
        return "\n".join(b.to_srt_block() for b in blocks)

    def write_all(self, base_srt_path: Path, mode: str = "both", max_duration: float | None = None) -> list[Path]:
        """
        Writes one or both SRT versions to disk.
        Returns a list of created file paths.
        """
        paths: list[Path] = []
        base_srt_path = Path(base_srt_path)
        
        stem = base_srt_path.stem
        suffix = base_srt_path.suffix
        parent = base_srt_path.parent
        
        if mode in ("phrase", "both"):
            phrase_path = parent / f"{stem}.phrase{suffix}"
            try:
                phrase_path.write_text(self.generate_v1_phrase_srt(max_duration), encoding="utf-8")
                paths.append(phrase_path)
                logger.info("Script subtitles (phrase) saved: %s", phrase_path)
            except Exception as e:
                logger.error("Failed to write phrase subtitles: %s", e)

        if mode in ("word", "both"):
            word_path = parent / f"{stem}.word{suffix}"
            try:
                word_path.write_text(self.generate_v2_word_srt(max_duration), encoding="utf-8")
                paths.append(word_path)
                logger.info("Script subtitles (word) saved: %s", word_path)
            except Exception as e:
                logger.error("Failed to write word subtitles: %s", e)
                
        return paths
