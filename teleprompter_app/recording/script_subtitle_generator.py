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
        self.phrases: list[list[int]] = []
        
        if not self.tokens:
            return

        # Improved splitting: First by line breaks
        lines = self.script_text.splitlines()
        token_ptr = 0
        
        for line in lines:
            line_text = line.strip()
            if not line_text:
                continue
                
            # For each line, further split into manageable phrases by punctuation or length
            # to avoid giant blocks if a line is very long.
            words_in_line = line_text.split()
            current_phrase_indices = []
            
            for word in words_in_line:
                if token_ptr >= len(self.tokens):
                    break
                
                current_phrase_indices.append(token_ptr)
                token_ptr += 1
                
                # Check if we should end the phrase here:
                # 1. Word ends with punctuation (sentence end)
                # 2. Phrase has reached a reasonable size (e.g., 8 words)
                if word.endswith(('.', '?', '!', ';', ':')) or len(current_phrase_indices) >= 8:
                    self.phrases.append(current_phrase_indices)
                    current_phrase_indices = []
            
            if current_phrase_indices:
                self.phrases.append(current_phrase_indices)

    def _get_interpolated_timestamps(self, timeline: SubtitleTimeline, max_duration: float | None = None) -> dict[int, float]:
        """
        Ensures tokens between matched events have timestamps by interpolating.
        Does NOT extrapolate beyond the first/last matched words.
        """
        events = timeline.get_events()
        if not events or not self.tokens:
            return {ev.token_index: ev.timestamp for ev in events} if events else {}

        # Map known events
        token_times = {ev.token_index: ev.timestamp for ev in events}
        
        # Sort indices to find gaps
        sorted_indices = sorted(token_times.keys())
        if not sorted_indices:
            return {}

        # Interpolate gaps between matched tokens
        for i in range(len(sorted_indices) - 1):
            idx_start = sorted_indices[i]
            idx_end = sorted_indices[i+1]
            time_start = token_times[idx_start]
            time_end = token_times[idx_end]
            
            num_missing = idx_end - idx_start - 1
            if num_missing > 0:
                step = (time_end - time_start) / (num_missing + 1)
                for j in range(1, num_missing + 1):
                    token_times[idx_start + j] = time_start + step * j
        
        return token_times

    def generate_v1_phrase_srt(self, timeline: SubtitleTimeline, max_duration: float | None = None) -> str:
        """v1: Grouped words, only including phrases that were actually reached."""
        token_times = self._get_interpolated_timestamps(timeline, max_duration)
        if not token_times:
            return ""
        
        blocks: list[SubtitleBlock] = []
        block_idx = 1
        
        for phrase_indices in self.phrases:
            # Find the words in this phrase that have timestamps
            reached_indices = [idx for idx in phrase_indices if idx in token_times]
            if not reached_indices:
                continue
                
            start_time = token_times[reached_indices[0]]
            
            # Phrase end is either the start of the next reached word, 
            # or the end of its own last reached word + buffer.
            last_reached_idx = reached_indices[-1]
            
            # Find next reached word in script
            next_reached_idx = None
            for i in range(last_reached_idx + 1, len(self.tokens)):
                if i in token_times:
                    next_reached_idx = i
                    break
            
            if next_reached_idx is not None:
                end_time = token_times[next_reached_idx]
            else:
                # Use max_duration if available, otherwise just a buffer
                end_time = max_duration or (token_times[last_reached_idx] + 0.8)
            
            # Construct text only from the reached words in this phrase
            # (or the whole phrase text if we want the subtitle to show the full line)
            # The user said they want to show phrase along with highlight text, 
            # so they likely want the full phrase text even if they didn't finish it?
            # Actually, if they spoke part of it, showing the whole line is common.
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
        """v2: Every word in the script with interpolated timestamps."""
        token_times = self._get_interpolated_timestamps(timeline, max_duration)
        if not token_times or not self.tokens:
            return ""
            
        blocks: list[SubtitleBlock] = []
        
        for i in range(len(self.tokens)):
            if i not in token_times:
                continue
                
            start_time = token_times[i]
            
            # End time is the start of the next word
            if i + 1 in token_times:
                end_time = token_times[i+1]
            else:
                end_time = start_time + 0.3 # Default word duration
                
            if max_duration is not None and start_time >= max_duration:
                break
                
            display_end = min(end_time, max_duration) if max_duration is not None else end_time
            token_text = self.tokens[i].text

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
