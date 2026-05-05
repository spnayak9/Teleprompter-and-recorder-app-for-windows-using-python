from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List

@dataclass(frozen=True, slots=True)
class SubtitleEvent:
    timestamp: float  # Relative to recording start
    token_index: int

class SubtitleTimeline:
    """Records the timing of when each word was highlighted during a recording session."""

    def __init__(self) -> None:
        self.events: List[SubtitleEvent] = []
        self._start_time: float | None = None

    def start(self) -> None:
        self._start_time = time.monotonic()
        self.events = []

    def record_highlight(self, token_index: int) -> None:
        if self._start_time is None:
            return
        
        elapsed = time.monotonic() - self._start_time
        # Only record if the index changed or if it's the first event
        if not self.events or self.events[-1].token_index != token_index:
            self.events.append(SubtitleEvent(elapsed, token_index))

    def get_events(self) -> List[SubtitleEvent]:
        return self.events

    def is_active(self) -> bool:
        return self._start_time is not None
