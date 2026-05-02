"""Fuzzy text-to-speech alignment engine."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from teleprompter_app.core.tokenizer import Token, normalize_word
from teleprompter_app.speech.recognizer import RecognizedWord

try:
    from rapidfuzz import fuzz
except ImportError:  # rapidfuzz is preferred but difflib keeps the app usable.
    fuzz = None


@dataclass(slots=True)
class AlignmentMatch:
    """Result of aligning a spoken word to a script token."""

    token_index: int
    spoken_word: str
    token_word: str
    score: float
    confidence: float | None


class AlignmentEngine:
    """Maintain a moving pointer through the script while tolerating speech drift."""

    def __init__(
        self,
        lookahead: int = 14,
        recovery_lookahead: int = 42,
        start_lookahead: int = 8,
        threshold: float = 72.0,
    ) -> None:
        self.lookahead = lookahead
        self.recovery_lookahead = recovery_lookahead
        self.start_lookahead = start_lookahead
        self.threshold = threshold
        self.tokens: list[Token] = []
        self.next_index = 0
        self.last_match_index: int | None = None
        self.last_spoken_normalized = ""
        self.miss_count = 0

    @property
    def has_tokens(self) -> bool:
        return bool(self.tokens)

    def set_tokens(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.reset()

    def reset(self) -> None:
        self.next_index = 0
        self.last_match_index = None
        self.last_spoken_normalized = ""
        self.miss_count = 0

    def align_words(self, spoken_words: Iterable[RecognizedWord | str]) -> list[AlignmentMatch]:
        matches: list[AlignmentMatch] = []
        for item in spoken_words:
            if isinstance(item, RecognizedWord):
                spoken = item.word
                confidence = item.confidence
            else:
                spoken = item
                confidence = None

            match = self.align_word(spoken, confidence)
            if match is not None:
                matches.append(match)
        return matches

    def align_word(self, spoken: str, confidence: float | None = None) -> AlignmentMatch | None:
        if not self.tokens:
            return None

        normalized = normalize_word(spoken)
        if not normalized:
            return None

        if self._is_duplicate_repeat(normalized):
            return None

        search_start = self._search_start()
        search_end = self.next_index + self.lookahead
        if self.last_match_index is None:
            search_end = self.start_lookahead
        elif self.miss_count >= 4:
            search_end = self.next_index + self.recovery_lookahead

        match = self._best_match(normalized, search_start, search_end)
        if match is None:
            self.miss_count += 1
            return None

        token_index, score = match
        if token_index < self.next_index:
            self.miss_count += 1
            return None

        adaptive_threshold = self.threshold - min(self.miss_count * 3, 12)
        if score < adaptive_threshold:
            self.miss_count += 1
            return None

        self.miss_count = 0
        self.last_match_index = token_index
        self.last_spoken_normalized = normalized

        self.next_index = token_index + 1

        token = self.tokens[token_index]
        return AlignmentMatch(token_index, spoken, token.text, score, confidence)

    def _search_start(self) -> int:
        if self.last_match_index is None:
            return 0
        return self.next_index

    def _is_duplicate_repeat(self, normalized: str) -> bool:
        """Ignore repeated recognizer output unless the script itself repeats next."""

        if self.last_match_index is None or normalized != self.last_spoken_normalized:
            return False
        if self.next_index >= len(self.tokens):
            return True
        return self.tokens[self.next_index].normalized != normalized

    def _best_match(self, spoken: str, start: int, end: int) -> tuple[int, float] | None:
        best_index: int | None = None
        best_score = 0.0
        end = min(end, len(self.tokens))

        for index in range(start, end):
            token = self.tokens[index]
            if not token.normalized:
                continue

            lexical = self._ratio(spoken, token.normalized)
            phonetic = 100.0 if self._soundex(spoken) == self._soundex(token.normalized) else 0.0
            base_score = max(lexical, phonetic * 0.88)

            distance = index - self.next_index
            position_bonus = max(0.0, 10.0 - (distance * 1.25))
            score = base_score + position_bonus

            if score > best_score:
                best_index = index
                best_score = score

        if best_index is None:
            return None

        return best_index, min(best_score, 100.0)

    def _ratio(self, left: str, right: str) -> float:
        if left == right:
            return 100.0
        if fuzz is not None:
            return float(fuzz.ratio(left, right))
        return SequenceMatcher(None, left, right).ratio() * 100.0

    def _soundex(self, word: str) -> str:
        if not word:
            return ""

        first = word[0].upper()
        groups = {
            "bfpv": "1",
            "cgjkqsxz": "2",
            "dt": "3",
            "l": "4",
            "mn": "5",
            "r": "6",
        }
        mapping = {char: code for chars, code in groups.items() for char in chars}

        digits = [mapping.get(char, "0") for char in word[1:].lower()]
        compacted: list[str] = []
        previous = ""
        for digit in digits:
            if digit != previous:
                compacted.append(digit)
            previous = digit

        compacted = [digit for digit in compacted if digit != "0"]
        return (first + "".join(compacted) + "000")[:4]
