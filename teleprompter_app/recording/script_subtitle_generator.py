from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from teleprompter_app.recording.subtitle_timeline import SubtitleTimeline, SubtitleEvent

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
    Generates subtitles from recorded teleprompter progression.
    Ground truth is the SubtitleTimeline captured during recording.
    """

    def __init__(self, script_text: str, tokens: list[Any] = None) -> None:
        self.script_text = script_text.strip()
        self.tokens = tokens or []
        
        # Phrases (v1): Groups of words based on line breaks
        self.phrases: list[list[int]] = []
        current_phrase = []
        
        # We need to know which words belong to which phrase (line)
        lines = self.script_text.splitlines()
        token_ptr = 0
        for line in lines:
            line_text = line.strip()
            if not line_text:
                continue
            
            phrase_token_indices = []
            words_in_line = line_text.split()
            for _ in words_in_line:
                if token_ptr < len(self.tokens):
                    phrase_token_indices.append(token_ptr)
                    token_ptr += 1
            
            if phrase_token_indices:
                self.phrases.append(phrase_token_indices)

    def generate_v1_phrase_srt(self, timeline: SubtitleTimeline, max_duration: float | None = None) -> str:
        """v1: Grouped words based on line breaks, using actual timestamps."""
        events = timeline.get_events()
        if not events:
            return ""

        # Map token index to timestamp
        token_times = {ev.token_index: ev.timestamp for ev in events}
        
        blocks: list[SubtitleBlock] = []
        block_idx = 1
        
        for phrase_indices in self.phrases:
            # Find start time (first word of phrase that has an event)
            start_time = None
            for idx in phrase_indices:
                if idx in token_times:
                    start_time = token_times[idx]
                    break
            
            if start_time is None:
                continue
                
            # Find end time (start time of the word after the last word of this phrase)
            last_idx = phrase_indices[-1]
            end_time = None
            
            # Find the very next event after the last word of this phrase
            for ev in events:
                if ev.token_index > last_idx:
                    end_time = ev.timestamp
                    break
            
            if end_time is None:
                end_time = max_duration or (events[-1].timestamp + 1.0)

            # Construct phrase text from tokens
            phrase_text = " ".join(self.tokens[i].text for i in phrase_indices if i < len(self.tokens))

            if max_duration is not None and start_time >= max_duration:
                break
            
            display_end = min(end_time, max_duration) if max_duration is not None else end_time

            blocks.append(SubtitleBlock(
                index=block_idx,
                start=start_time,
                end=display_end,
                text=phrase_text
            ))
            block_idx += 1
            
        return "\n".join(b.to_srt_block() for b in blocks)

    def generate_v2_word_srt(self, timeline: SubtitleTimeline, max_duration: float | None = None) -> str:
        """v2: Single-word subtitles using actual timestamps."""
        events = timeline.get_events()
        if not events:
            return ""
            
        blocks: list[SubtitleBlock] = []
        
        for i, event in enumerate(events):
            start_time = event.timestamp
            
            # End time is the start of the next word event
            if i + 1 < len(events):
                end_time = events[i+1].timestamp
            else:
                end_time = max_duration or (start_time + 1.0)

            if max_duration is not None and start_time >= max_duration:
                break
                
            display_end = min(end_time, max_duration) if max_duration is not None else end_time
            
            token_text = self.tokens[event.token_index].text if event.token_index < len(self.tokens) else "?"

            blocks.append(SubtitleBlock(
                index=i + 1,
                start=start_time,
                end=display_end,
                text=token_text
            ))
            
        return "\n".join(b.to_srt_block() for b in blocks)

    def write_all(self, base_srt_path: Path, timeline: SubtitleTimeline, mode: str = "both", max_duration: float | None = None) -> list[Path]:
        """
        Writes one or both SRT versions to disk using recorded timeline.
        """
        paths: list[Path] = []
        base_srt_path = Path(base_srt_path)
        stem = base_srt_path.stem
        suffix = base_srt_path.suffix
        parent = base_srt_path.parent
        
        if mode in ("phrase", "both"):
            phrase_path = parent / f"{stem}.phrase{suffix}"
            try:
                phrase_path.write_text(self.generate_v1_phrase_srt(timeline, max_duration), encoding="utf-8")
                paths.append(phrase_path)
                logger.info("Script subtitles (phrase) saved: %s", phrase_path)
            except Exception as e:
                logger.error("Failed to write phrase subtitles: %s", e)

        if mode in ("word", "both"):
            word_path = parent / f"{stem}.word{suffix}"
            try:
                word_path.write_text(self.generate_v2_word_srt(timeline, max_duration), encoding="utf-8")
                paths.append(word_path)
                logger.info("Script subtitles (word) saved: %s", word_path)
            except Exception as e:
                logger.error("Failed to write word subtitles: %s", e)
                
        return paths
