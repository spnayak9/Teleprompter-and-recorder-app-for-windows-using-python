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


FILLER_WORDS = {"ah", "er", "erm", "hm", "hmm", "uh", "um"}


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
        recovery_lookahead: int = 18,
        initial_recovery_lookahead: int = 6,
        short_phrase_lookahead: int = 6,
        threshold: float = 68.0,
        phrase_threshold: float = 82.0,
        phrase_words: int = 2,
        max_pending_words: int = 5,
    ) -> None:
        self.recovery_lookahead = recovery_lookahead
        self.initial_recovery_lookahead = initial_recovery_lookahead
        self.short_phrase_lookahead = short_phrase_lookahead
        self.threshold = threshold
        self.phrase_threshold = phrase_threshold
        self.phrase_words = phrase_words
        self.max_pending_words = max_pending_words
        self.tokens: list[Token] = []
        self.next_index = 0
        self.last_match_index: int | None = None
        self.last_spoken_normalized = ""
        self.pending_spoken: list[tuple[str, str, float | None]] = []
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
        self.pending_spoken = []
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

        if normalized in FILLER_WORDS:
            return None

        if self._is_duplicate_repeat(normalized):
            return None

        expected_match = self._match_expected_word(normalized, spoken, confidence)
        if expected_match is not None:
            return expected_match

        self._remember_pending(normalized, spoken, confidence)
        phrase_match = self._match_confirmed_phrase()
        if phrase_match is not None:
            return phrase_match

        self.miss_count += 1
        return None

    def _match_expected_word(
        self,
        normalized: str,
        spoken: str,
        confidence: float | None,
    ) -> AlignmentMatch | None:
        if self.next_index >= len(self.tokens):
            return None

        token = self.tokens[self.next_index]
        score = self._word_score(normalized, token.normalized)
        if not self._passes_word_threshold(normalized, token.normalized, score, self.threshold):
            return None

        self.pending_spoken = []
        return self._accept(self.next_index, spoken, confidence, score)

    def _match_confirmed_phrase(self) -> AlignmentMatch | None:
        if len(self.pending_spoken) < self.phrase_words or self.next_index >= len(self.tokens):
            return None

        max_words = min(len(self.pending_spoken), self.max_pending_words)
        recovery_lookahead = (
            self.initial_recovery_lookahead
            if self.last_match_index is None
            else self.recovery_lookahead
        )
        search_end = min(len(self.tokens), self.next_index + recovery_lookahead)

        for phrase_size in range(max_words, self.phrase_words - 1, -1):
            phrase = self.pending_spoken[-phrase_size:]
            best: tuple[int, float] | None = None

            for candidate in range(self.next_index, search_end - phrase_size + 1):
                scores = [
                    self._word_score(spoken_word, self.tokens[candidate + offset].normalized)
                    for offset, (spoken_word, _raw, _confidence) in enumerate(phrase)
                ]
                if not self._passes_phrase_threshold(phrase, candidate, scores):
                    continue

                distance_penalty = max(0, candidate - self.next_index) * 2.0
                phrase_score = (sum(scores) / len(scores)) - distance_penalty
                if best is None or phrase_score > best[1]:
                    best = (candidate, phrase_score)

            if best is not None:
                candidate, phrase_score = best
                target_index = candidate + phrase_size - 1
                _normalized, raw, phrase_confidence = phrase[-1]
                self.pending_spoken = []
                return self._accept(target_index, raw, phrase_confidence, phrase_score)

        return None

    def _accept(
        self,
        token_index: int,
        spoken: str,
        confidence: float | None,
        score: float,
    ) -> AlignmentMatch:
        token = self.tokens[token_index]
        self.miss_count = 0
        self.last_match_index = token_index
        self.last_spoken_normalized = normalize_word(spoken)
        self.next_index = token_index + 1
        return AlignmentMatch(token_index, spoken, token.text, min(score, 100.0), confidence)

    def _remember_pending(self, normalized: str, spoken: str, confidence: float | None) -> None:
        self.pending_spoken.append((normalized, spoken, confidence))
        if len(self.pending_spoken) > self.max_pending_words:
            self.pending_spoken = self.pending_spoken[-self.max_pending_words :]

    def _is_duplicate_repeat(self, normalized: str) -> bool:
        """Ignore repeated recognizer output unless the script itself repeats next."""

        if self.last_match_index is None or normalized != self.last_spoken_normalized:
            return False
        if self.next_index >= len(self.tokens):
            return True
        return self.tokens[self.next_index].normalized != normalized

    def _passes_phrase_threshold(
        self,
        phrase: list[tuple[str, str, float | None]],
        token_index: int,
        scores: list[float],
    ) -> bool:
        if not scores:
            return False

        distance = token_index - self.next_index
        if len(phrase) <= self.phrase_words and distance > self.short_phrase_lookahead:
            return False

        for offset, (spoken_word, _raw, _confidence) in enumerate(phrase):
            token_word = self.tokens[token_index + offset].normalized
            if not self._passes_word_threshold(spoken_word, token_word, scores[offset], self.threshold):
                return False

        return (sum(scores) / len(scores)) >= self.phrase_threshold

    def _passes_word_threshold(
        self,
        spoken: str,
        token: str,
        score: float,
        threshold: float,
    ) -> bool:
        if not token:
            return False
        if spoken == token:
            return True
        if min(len(spoken), len(token)) <= 3:
            return False
        return score >= threshold

    def _word_score(self, spoken: str, token: str) -> float:
        if spoken == token:
            return 100.0
        lexical = self._ratio(spoken, token)
        if min(len(spoken), len(token)) <= 3:
            return lexical

        phonetic = 100.0 if self._soundex(spoken) == self._soundex(token) else 0.0
        return max(lexical, phonetic * 0.88)

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
