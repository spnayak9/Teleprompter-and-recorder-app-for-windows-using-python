from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout, QLabel
)
from PySide6.QtCore import Signal, Qt

class ScriptEditorDialog(QDialog):
    """Simple editor for pasting or typing scripts directly."""
    script_updated = Signal(str)

    def __init__(self, current_text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Script Editor")
        self.setMinimumSize(600, 400)
        
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("Paste or type your script below:"))
        
        self.editor = QTextEdit()
        self.editor.setPlainText(current_text)
        self.editor.setPlaceholderText("Enter your script here...")
        layout.addWidget(self.editor)
        
        btn_layout = QHBoxLayout()
        self.apply_btn = QPushButton("Apply Script")
        self.apply_btn.clicked.connect(self._on_apply)
        
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.editor.clear)
        
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        
        btn_layout.addWidget(self.clear_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.apply_btn)
        btn_layout.addWidget(self.close_btn)
        
        layout.addLayout(btn_layout)

    def _on_apply(self) -> None:
        text = self.editor.toPlainText().strip()
        if text:
            self.script_updated.emit(text)
            self.accept()
