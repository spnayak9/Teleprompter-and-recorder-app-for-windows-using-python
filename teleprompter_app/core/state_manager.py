"""Runtime state container for the teleprompter session."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RuntimeState:
    current_word_index: int = -1
    scroll_position: int = 0
    confidence: float | None = None
    is_listening: bool = False
    last_spoken_text: str = ""


class StateManager:
    """Store and update mutable runtime state outside the UI layer."""

    def __init__(self) -> None:
        self.state = RuntimeState()

    def reset(self) -> None:
        listening = self.state.is_listening
        self.state = RuntimeState(is_listening=listening)

    def set_listening(self, value: bool) -> None:
        self.state.is_listening = value

    def update_word(
        self,
        word_index: int,
        confidence: float | None = None,
        spoken_text: str = "",
    ) -> None:
        self.state.current_word_index = word_index
        self.state.confidence = confidence
        if spoken_text:
            self.state.last_spoken_text = spoken_text

    def set_last_spoken_text(self, value: str) -> None:
        self.state.last_spoken_text = value

    def set_scroll_position(self, value: int) -> None:
        self.state.scroll_position = value
