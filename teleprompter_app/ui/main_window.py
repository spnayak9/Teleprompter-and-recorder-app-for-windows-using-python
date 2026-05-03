"""Main window composition for the teleprompter desktop app."""

from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMainWindow, QDockWidget

from teleprompter_app.audio.mic_manager import MicrophoneDevice
from teleprompter_app.core.tokenizer import Token
from teleprompter_app.ui.settings_panel import SettingsPanel
from teleprompter_app.ui.teleprompter_view import TeleprompterView
from teleprompter_app.ui_main import PreviewOverlay, RecordingControls
from teleprompter_app.utils.config import AppSettings
from teleprompter_app.ui_config import ConfigDialog


class MainWindow(QMainWindow):
    """Application shell with teleprompter view, settings dock, and toolbar."""

    script_file_selected = Signal(str, str)
    start_requested = Signal()
    stop_requested = Signal()
    rewind_requested = Signal()
    settings_changed = Signal(dict)
    microphones_refresh_requested = Signal()
    start_recording_requested = Signal()
    stop_recording_requested = Signal()
    select_recording_dir_requested = Signal()

    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle("AI Teleprompter with Real-Time Speech Highlighting")
        self.resize(1280, 820)

        self.teleprompter = TeleprompterView(settings)
        # Wrap teleprompter in preview overlay (preview hidden by default)
        self.preview_overlay = PreviewOverlay(self.teleprompter)
        self.setCentralWidget(self.preview_overlay)

        self.settings_panel = SettingsPanel(settings)
        self.settings_dock = QDockWidget("Settings", self)
        self.settings_dock.setWidget(self.settings_panel)
        self.settings_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.settings_dock)

        self._build_toolbar()
        self._connect_signals()
        self.statusBar().showMessage("Ready")

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Controls")
        toolbar.setMovable(False)

        self.open_action = QAction("Open Script", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.start_action = QAction("Start", self)
        self.start_action.setShortcut("Ctrl+R")
        self.stop_action = QAction("Stop", self)
        self.stop_action.setShortcut("Ctrl+.")
        self.stop_action.setEnabled(False)
        self.rewind_action = QAction("Rewind", self)
        self.rewind_action.setShortcut("Ctrl+Home")

        toolbar.addAction(self.open_action)
        self.config_action = QAction("Configure", self)
        toolbar.addAction(self.config_action)
        toolbar.addSeparator()
        toolbar.addAction(self.start_action)
        toolbar.addAction(self.stop_action)
        toolbar.addAction(self.rewind_action)
        # Recording controls widget (compact)
        self.recording_controls = RecordingControls(self)
        toolbar.addWidget(self.recording_controls)

    def _connect_signals(self) -> None:
        self.open_action.triggered.connect(lambda _checked=False: self._choose_script())
        self.config_action.triggered.connect(lambda _checked=False: self._open_config())
        self.start_action.triggered.connect(lambda _checked=False: self.start_requested.emit())
        self.stop_action.triggered.connect(lambda _checked=False: self.stop_requested.emit())
        self.rewind_action.triggered.connect(lambda _checked=False: self.rewind_requested.emit())
        self.recording_controls.start_btn.clicked.connect(lambda _checked=False: self.start_recording_requested.emit())
        self.recording_controls.stop_btn.clicked.connect(lambda _checked=False: self.stop_recording_requested.emit())

        self.settings_panel.start_requested.connect(self.start_requested.emit)
        self.settings_panel.stop_requested.connect(self.stop_requested.emit)
        self.settings_panel.settings_changed.connect(self.settings_changed.emit)
        self.settings_panel.refresh_microphones_requested.connect(self.microphones_refresh_requested.emit)
        self.settings_panel.start_recording_requested.connect(self.start_recording_requested.emit)
        self.settings_panel.stop_recording_requested.connect(self.stop_recording_requested.emit)
        self.settings_panel.select_recording_dir_requested.connect(self.select_recording_dir_requested.emit)

    def _choose_script(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open teleprompter script",
            "",
            "Scripts (*.txt *.md *.markdown *.html *.htm);;All files (*.*)",
        )
        if file_path:
            self.script_file_selected.emit(file_path, self.settings_panel.current_input_mode())

    def set_document(self, html: str, tokens: list[Token]) -> None:
        self.teleprompter.set_tokenized_html(html, tokens)

    def highlight_word(self, index: int, confidence: float | None = None) -> None:
        self.teleprompter.highlight_word(index, confidence)
        if index >= 0:
            conf = "" if confidence is None else f" confidence {confidence:.2f}"
            self.statusBar().showMessage(f"Matched word {index + 1}{conf}")

    def apply_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self.settings_panel.apply_settings(settings)
        self.teleprompter.apply_settings(settings)

    def set_microphones(self, devices: list[MicrophoneDevice], selected_index: int | None) -> None:
        self.settings_panel.set_microphones(devices, selected_index)

    def set_listening(self, listening: bool) -> None:
        self.start_action.setEnabled(not listening)
        self.stop_action.setEnabled(listening)
        self.settings_panel.set_listening(listening)

    def set_recording(self, recording: bool, status: str = "") -> None:
        self.settings_panel.set_recording(recording, status)

    def set_recording_status(self, status: str) -> None:
        self.settings_panel.set_recording_status(status)

    def set_recording_directory(self, directory: str) -> None:
        self.settings_panel.set_recording_directory(directory)

    def choose_project_folder(self, initial_directory: str = "") -> str:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select or create recording project folder",
            initial_directory,
            QFileDialog.Option.ShowDirsOnly,
        )
        return directory

    def _open_config(self) -> None:
        dialog = ConfigDialog(parent=self)
        dialog.saved.connect(lambda _s: None)
        dialog.exec()

    def set_status(self, message: str) -> None:
        self.statusBar().showMessage(message)
