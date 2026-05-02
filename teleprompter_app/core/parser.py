"""Input parsing and normalization into a unified HTML representation."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from bs4 import BeautifulSoup, Comment


class InputType(str, Enum):
    """Supported script input formats."""

    PLAIN = "plain"
    MARKDOWN = "markdown"
    HTML = "html"


@dataclass(slots=True)
class ParsedDocument:
    """Normalized script document."""

    source_path: Path | None
    input_type: InputType
    html: str
    plain_text: str


class ScriptParser:
    """Detect and convert plain text, Markdown, and HTML scripts."""

    _extension_map = {
        ".txt": InputType.PLAIN,
        ".text": InputType.PLAIN,
        ".md": InputType.MARKDOWN,
        ".markdown": InputType.MARKDOWN,
        ".html": InputType.HTML,
        ".htm": InputType.HTML,
    }

    def parse_file(self, path: Path, input_type: InputType | None = None) -> ParsedDocument:
        text = path.read_text(encoding="utf-8")
        detected = input_type or self.detect_input_type(path)
        return self.parse_text(text, detected, path)

    def parse_text(
        self,
        text: str,
        input_type: InputType = InputType.PLAIN,
        source_path: Path | None = None,
    ) -> ParsedDocument:
        if input_type == InputType.PLAIN:
            html_text = self._plain_to_html(text)
        elif input_type == InputType.MARKDOWN:
            html_text = self._markdown_to_html(text)
        elif input_type == InputType.HTML:
            html_text = text
        else:
            raise ValueError(f"Unsupported input type: {input_type}")

        clean_html = self.clean_html(html_text)
        plain_text = BeautifulSoup(clean_html, "html.parser").get_text(" ", strip=True)
        return ParsedDocument(source_path, input_type, clean_html, plain_text)

    def detect_input_type(self, path: Path) -> InputType:
        suffix = path.suffix.lower()
        if suffix not in self._extension_map:
            raise ValueError(
                f"Unsupported file extension '{suffix}'. Use .txt, .md, .markdown, .html, or .htm."
            )
        return self._extension_map[suffix]

    def clean_html(self, value: str) -> str:
        """Remove unsafe tags and normalize to body-level HTML."""

        soup = BeautifulSoup(value, "html.parser")

        for node in soup.find_all(["script", "style", "iframe", "object", "embed", "meta", "link"]):
            node.decompose()

        for comment in soup.find_all(string=lambda item: isinstance(item, Comment)):
            comment.extract()

        allowed_attrs = {"href", "title", "alt", "class"}
        for tag in soup.find_all(True):
            attrs = dict(tag.attrs)
            for attr in attrs:
                if attr not in allowed_attrs:
                    del tag.attrs[attr]
            if tag.name == "a" and tag.get("href", "").lower().startswith("javascript:"):
                del tag.attrs["href"]

        body = soup.body
        if body is not None:
            return "".join(str(child) for child in body.children).strip()

        return str(soup).strip()

    def _plain_to_html(self, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        paragraphs = [block.strip("\n") for block in re.split(r"\n\s*\n", normalized) if block.strip()]
        if not paragraphs:
            return "<p></p>"

        rendered = []
        for paragraph in paragraphs:
            escaped_lines = [html.escape(line) for line in paragraph.split("\n")]
            rendered.append(f"<p>{'<br/>'.join(escaped_lines)}</p>")
        return "\n".join(rendered)

    def _markdown_to_html(self, value: str) -> str:
        try:
            import markdown
        except ImportError as exc:
            raise RuntimeError("Markdown support requires the 'markdown' package.") from exc

        return markdown.markdown(
            value,
            extensions=["extra", "sane_lists", "nl2br"],
            output_format="html5",
        )
