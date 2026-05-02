"""Abstract speech recognition interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class RecognizedWord:
    """One recognized word from a speech engine."""

    word: str
    start: float | None = None
    end: float | None = None
    confidence: float | None = None
    is_final: bool = False


@dataclass(slots=True)
class RecognitionResult:
    """Speech recognition result payload."""

    text: str
    words: list[RecognizedWord]
    is_final: bool


ResultCallback = Callable[[RecognitionResult], None]
StatusCallback = Callable[[str], None]
ErrorCallback = Callable[[str], None]


class SpeechRecognizer(ABC):
    """Base class for speech recognition engines.

    New engines, such as Whisper, can implement this contract without changing
    the UI or alignment code.
    """

    @property
    @abstractmethod
    def is_running(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def start(
        self,
        on_result: ResultCallback,
        on_status: StatusCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError
