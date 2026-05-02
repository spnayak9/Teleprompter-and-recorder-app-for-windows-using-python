"""Scrollable teleprompter text renderer with real-time highlighting."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QFrame, QTextBrowser, QTextEdit

from teleprompter_app.core.tokenizer import Token
from teleprompter_app.utils.config import AppSettings


class TeleprompterView(QTextBrowser):
    """Render tokenized HTML and keep the spoken word centered."""

    def __init__(self, settings: AppSettings, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.settings = settings
        self.tokens: list[Token] = []
        self.raw_html = ""
        self.current_index = -1
        self.progress_index = -1
        self.painted_progress_index = -1
        self._scroll_animation = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setReadOnly(True)
        self.setOpenExternalLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.document().setDocumentMargin(44)
        self.apply_settings(settings)

    def set_tokenized_html(self, html: str, tokens: list[Token]) -> None:
        self.raw_html = html
        self.tokens = tokens
        self.current_index = -1
        self.progress_index = -1
        self.painted_progress_index = -1
        self.setHtml(self._build_document_html())
        self._resolve_document_positions()
        self.highlight_word(-1)
        self.verticalScrollBar().setValue(0)

    def apply_settings(self, settings: AppSettings) -> None:
        current_index = self.current_index
        progress_index = self.progress_index
        self.settings = settings
        if self.raw_html:
            self.setHtml(self._build_document_html())
            self._resolve_document_positions()
            self.current_index = -1
            self.progress_index = -1
            self.painted_progress_index = -1
            if progress_index >= 0:
                self._apply_progress_format(0, progress_index)
                self.progress_index = progress_index
                self.painted_progress_index = progress_index
            self._show_current_word(current_index, scroll=False)

    def highlight_word(self, index: int, confidence: float | None = None) -> None:
        if index < 0:
            self._clear_progress_format()
            self.current_index = -1
            self.progress_index = -1
            self.painted_progress_index = -1
            self.setExtraSelections([])
            return

        self.current_index = index
        if index > self.progress_index:
            start = self.progress_index + 1
            self.progress_index = index
            self._apply_progress_format(start, index)

        self._show_current_word(index, scroll=True)

    def _show_current_word(self, index: int, scroll: bool) -> None:
        selections: list[QTextEdit.ExtraSelection] = []

        if 0 <= index < len(self.tokens):
            token = self.tokens[index]
            cursor = self._cursor_for_token(token)
            if not cursor.isNull():
                selection = QTextEdit.ExtraSelection()
                selection.cursor = cursor
                selection.format.setBackground(QColor(self.settings.highlight_color))
                selection.format.setForeground(QColor(self.settings.highlight_text_color))
                selections.append(selection)

                if scroll:
                    self._center_cursor(cursor)

        self.setExtraSelections(selections)
        self.viewport().update()

    def _apply_progress_format(self, start_index: int, end_index: int) -> None:
        if end_index <= self.painted_progress_index:
            return

        start_index = max(start_index, self.painted_progress_index + 1)
        cursor = self._cursor_for_token_range(start_index, end_index)
        if cursor.isNull():
            return

        progress_format = QTextCharFormat()
        progress_format.setBackground(self._progress_color())
        cursor.mergeCharFormat(progress_format)
        self.painted_progress_index = end_index

    def _clear_progress_format(self) -> None:
        if self.painted_progress_index < 0 or not self.raw_html:
            return
        scroll_position = self.verticalScrollBar().value()
        self.setHtml(self._build_document_html())
        self._resolve_document_positions()
        self.verticalScrollBar().setValue(scroll_position)

    def _cursor_for_token(self, token: Token) -> QTextCursor:
        return self._cursor_for_token_range(token.index, token.index)

    def _cursor_for_token_range(self, start_index: int, end_index: int) -> QTextCursor:
        cursor = QTextCursor(self.document())
        if not self.tokens:
            return cursor

        start_token = self.tokens[max(0, start_index)]
        end_token = self.tokens[min(len(self.tokens) - 1, end_index)]
        start = start_token.doc_start if start_token.doc_start is not None else start_token.start_char
        end = end_token.doc_end if end_token.doc_end is not None else end_token.end_char
        cursor.setPosition(max(0, start))
        cursor.setPosition(max(0, end), QTextCursor.MoveMode.KeepAnchor)
        return cursor

    def _progress_color(self) -> QColor:
        color = QColor(self.settings.highlight_color)
        if not color.isValid():
            color = QColor("#ffd166")
        background = QColor("#111318")
        blend = 0.42
        return QColor(
            int((color.red() * blend) + (background.red() * (1 - blend))),
            int((color.green() * blend) + (background.green() * (1 - blend))),
            int((color.blue() * blend) + (background.blue() * (1 - blend))),
        )

    def _center_cursor(self, cursor: QTextCursor) -> None:
        rect = self.cursorRect(cursor)
        scroll_bar = self.verticalScrollBar()
        target = scroll_bar.value() + rect.center().y() - (self.viewport().height() // 2)
        target = max(scroll_bar.minimum(), min(scroll_bar.maximum(), target))

        duration = max(20, 220 - (self.settings.scroll_speed * 2))
        self._scroll_animation.stop()
        self._scroll_animation.setDuration(duration)
        self._scroll_animation.setStartValue(scroll_bar.value())
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.start()

    def _build_document_html(self) -> str:
        base_css = self._load_base_css()
        weight = "700" if self.settings.bold else "400"
        style = "italic" if self.settings.italic else "normal"
        decoration = "underline" if self.settings.underline else "none"
        dynamic_css = f"""
            body {{
                background: #111318;
                color: {self.settings.text_color};
                font-family: "{self.settings.font_family}", sans-serif;
                font-size: {self.settings.font_size}px;
                font-weight: {weight};
                font-style: {style};
                text-decoration: {decoration};
                line-height: 1.65;
            }}
            .prompter {{
                max-width: 1180px;
                margin: 0 auto;
                padding: 42vh 24px;
            }}
            .tp-word {{
                border-radius: 5px;
                padding: 0 0.03em;
            }}
        """
        return f"""
        <!doctype html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>{base_css}\n{dynamic_css}</style>
        </head>
        <body>
            <main class="prompter">{self.raw_html}</main>
        </body>
        </html>
        """

    def _load_base_css(self) -> str:
        css_path = Path(__file__).resolve().parents[1] / "assets" / "styles.css"
        if css_path.exists():
            return css_path.read_text(encoding="utf-8")
        return ""

    def _resolve_document_positions(self) -> None:
        text = self.document().toPlainText()
        search_from = 0
        lowered = text.casefold()

        for token in self.tokens:
            needle = token.text.casefold()
            index = lowered.find(needle, search_from)
            if index < 0:
                index = lowered.find(needle)

            if index >= 0:
                token.doc_start = index
                token.doc_end = index + len(token.text)
                search_from = token.doc_end
            else:
                token.doc_start = None
                token.doc_end = None
