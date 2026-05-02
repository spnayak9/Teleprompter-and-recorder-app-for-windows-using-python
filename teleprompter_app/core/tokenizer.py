"""HTML tokenization and word-level wrapping for synchronized highlighting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[.'_-][A-Za-z0-9]+)*", re.UNICODE)


def normalize_word(value: str) -> str:
    """Normalize words for speech alignment."""

    normalized = value.casefold().strip()
    normalized = normalized.replace("'", "").replace(".", "")
    normalized = re.sub(r"[^a-z0-9]+", "", normalized)
    return normalized


@dataclass(slots=True)
class Token:
    """A script word token with both source and rendered document positions."""

    index: int
    text: str
    normalized: str
    start_char: int
    end_char: int
    doc_start: int | None = None
    doc_end: int | None = None


@dataclass(slots=True)
class TokenizedDocument:
    """Tokenized HTML and the ordered word tokens it contains."""

    html: str
    plain_text: str
    tokens: list[Token]


class ScriptTokenizer:
    """Wrap every word in the HTML with stable span IDs."""

    skipped_parent_tags = {"script", "style", "code", "pre"}

    def tokenize_html(self, html: str) -> TokenizedDocument:
        from bs4 import BeautifulSoup, NavigableString

        soup = BeautifulSoup(html, "html.parser")
        tokens: list[Token] = []
        text_cursor = 0

        text_nodes = [
            node
            for node in soup.find_all(string=True)
            if isinstance(node, NavigableString) and self._should_tokenize_node(node)
        ]

        for node in text_nodes:
            original = str(node)
            replacements: list[NavigableString] = []
            local_cursor = 0

            for match in WORD_PATTERN.finditer(original):
                if match.start() > local_cursor:
                    prefix = original[local_cursor : match.start()]
                    replacements.append(NavigableString(prefix))
                    text_cursor += len(prefix)

                word = match.group(0)
                normalized = normalize_word(word)
                span = soup.new_tag(
                    "span",
                    attrs={
                        "id": f"word-{len(tokens)}",
                        "class": "tp-word",
                        "data-index": str(len(tokens)),
                    },
                )
                span.string = word
                replacements.append(span)

                start = text_cursor
                text_cursor += len(word)
                tokens.append(Token(len(tokens), word, normalized, start, text_cursor))
                local_cursor = match.end()

            if local_cursor < len(original):
                suffix = original[local_cursor:]
                replacements.append(NavigableString(suffix))
                text_cursor += len(suffix)

            if replacements:
                for replacement in replacements:
                    node.insert_before(replacement)
                node.extract()
            else:
                text_cursor += len(original)

        return TokenizedDocument(str(soup), soup.get_text(), tokens)

    def _should_tokenize_node(self, node: Any) -> bool:
        parent = node.parent
        if parent is None:
            return False
        if parent.name in self.skipped_parent_tags:
            return False
        classes = parent.get("class", [])
        if "tp-word" in classes:
            return False
        return bool(str(node).strip())
