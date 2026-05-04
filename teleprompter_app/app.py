import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Slot, Signal
from PySide6.QtWidgets import QMessageBox

from teleprompter_app.utils.config import ConfigManager, AppSettings
from teleprompter_app.ui.main_window import MainWindow
from teleprompter_app.speech.recognizer import RecognitionResult
from teleprompter_app.speech.vosk_engine import VoskSpeechRecognizer
from teleprompter_app.core.parser import ScriptParser, InputType
from teleprompter_app.core.tokenizer import ScriptTokenizer
from teleprompter_app.core.alignment import AlignmentEngine
from teleprompter_app.audio.mic_manager import MicrophoneManager
from teleprompter_app.recorder import RecordingController
from teleprompter_app.preview import PreviewController
from teleprompter_app.system_probe import probe_system
from teleprompter_app.recording.file_manager import RecordingFiles, RecordingFileManager
from teleprompter_app.recording.audio_config import RecordingConfig

logger = logging.getLogger(__name__)

class RecognitionBridge(QObject):
    """Bridge between recognizer callbacks and Qt signals."""
    result_ready = Signal(RecognitionResult)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def on_result(self, result: RecognitionResult):
        self.result_ready.emit(result)

    def on_status(self, status: str):
        self.status_changed.emit(status)

    def on_error(self, message: str):
        self.error_occurred.emit(message)

class TeleprompterController(QObject):
    def __init__(self, qt_app=None):
        super().__init__()
        self.qt_app = qt_app
        self.settings_manager = ConfigManager()
        self.settings = self.settings_manager.load()
        
        # System Probe (Once at startup)
        logger.info("Probing system capabilities (once)...")
        self.system_profile = probe_system()
        
        # Controllers
        self.preview_controller = PreviewController()
        self.recording_controller = RecordingController()
        
        # Hardware Managers
        self.mic_manager = MicrophoneManager()
        self.file_manager = RecordingFileManager()
        
        # Core Logic
        self.parser = ScriptParser()
        self.tokenizer = ScriptTokenizer()
        self.alignment_engine = AlignmentEngine()
        
        # Recognition
        self.recognition_bridge = RecognitionBridge()
        self.recognizer: VoskSpeechRecognizer | None = None
        
        # UI
        self.window = MainWindow(self.settings, self.system_profile)
        
        # Timers
        self.recording_timer = QTimer(self)
        self.recording_timer.timeout.connect(self._update_recording_status)
        self.recording_started_at = None
        
        self._connect_signals()
        
        # Initial state
        self._refresh_microphones()
        self.apply_settings(self.settings.to_dict())

    def _connect_signals(self):
        # UI -> Controller
        self.window.script_file_selected.connect(self.load_script)
        self.window.start_requested.connect(self.start_listening)
        self.window.stop_requested.connect(self.stop_listening)
        self.window.rewind_requested.connect(self.rewind_script)
        self.window.settings_changed.connect(self.apply_settings)
        self.window.microphones_refresh_requested.connect(self._refresh_microphones)
        self.window.start_recording_requested.connect(self.start_recording)
        self.window.stop_recording_requested.connect(self.stop_recording)
        self.window.background_mode_changed.connect(self._on_background_mode_changed)
        self.window.preview_resolution_changed.connect(self._on_preview_resolution_changed)
        self.window.config_saved.connect(self._on_config_saved)
        
        # Preview -> UI
        self.preview_controller.frame_ready.connect(self.window.preview_overlay.set_frame)
        self.preview_controller.fps_ready.connect(self.window.preview_overlay.set_fps)
        self.preview_controller.error.connect(self._on_preview_error)
        
        # Recording -> UI
        self.recording_controller.started.connect(self._on_recording_started)
        self.recording_controller.stopped.connect(self._on_recording_stopped)
        self.recording_controller.error.connect(self._on_recording_error)
        
        # Recognition -> Controller/UI
        self.recognition_bridge.result_ready.connect(self._on_recognition_result)
        self.recognition_bridge.status_changed.connect(self.window.set_status)
        self.recognition_bridge.error_occurred.connect(self._on_recognition_error)

    def apply_settings(self, settings_dict: dict):
        self.settings = self.settings.updated(settings_dict)
        self.settings_manager.save(self.settings)
        
        # Update UI components
        self.window.apply_settings(self.settings)
        
        # Update preview if needed
        self._update_preview_state()

    def _update_preview_state(self):
        use_camera = getattr(self.settings, "use_camera_background", False)
        
        if use_camera:
            camera = self.system_profile.camera_by_ffmpeg_name(self.settings.video_device)
            if camera:
                mapping = {
                    "240p": (426, 240),
                    "360p": (640, 360),
                    "480p": (854, 480),
                    "720p": (1280, 720),
                }
                res_key = getattr(self.settings, "preview_resolution", "360p")
                width, height = mapping.get(res_key, (640, 360))
                
                self.preview_controller.start(camera.opencv_index, width, height)
                self.window.preview_overlay.enable_preview(True)
                return
        
        self.preview_controller.stop(wait=False)
        self.window.preview_overlay.enable_preview(False)

    def _on_background_mode_changed(self, mode: str):
        self.apply_settings({"use_camera_background": (mode == "camera")})

    def _on_preview_resolution_changed(self, res: str):
        self.apply_settings({"preview_resolution": res})

    def _on_config_saved(self):
        # Reload settings after config dialog closes
        self.settings = self.settings_manager.load()
        self.apply_settings({})

    def _refresh_microphones(self):
        devices = self.mic_manager.list_input_devices()
        selected = self.settings.microphone_index
        self.window.set_microphones(devices, selected if selected >= 0 else None)

    @Slot(str, str)
    def load_script(self, file_path: str, input_mode: str = ""):
        try:
            path = Path(file_path)
            mode = InputType(input_mode) if input_mode else None
            parsed = self.parser.parse_file(path, mode)
            tokenized = self.tokenizer.tokenize_html(parsed.html)
            
            self.alignment_engine.set_tokens(tokenized.tokens)
            self.window.set_document(tokenized.html, tokenized.tokens)
            self.window.set_status(f"Loaded script: {path.name}")
        except Exception as e:
            logger.exception("Failed to load script")
            QMessageBox.critical(self.window, "Error", f"Failed to load script: {e}")

    @Slot()
    def start_listening(self):
        if self.recognizer and self.recognizer.is_running:
            return
            
        if not self.alignment_engine.has_tokens:
            QMessageBox.warning(self.window, "No Script", "Please load a script first.")
            return

        try:
            self.recognizer = VoskSpeechRecognizer(
                model_path=self.settings.vosk_model_path,
                device_index=self.settings.microphone_index,
                sample_rate=self.settings.sample_rate,
            )
            self.recognizer.start(
                on_result=self.recognition_bridge.on_result,
                on_status=self.recognition_bridge.on_status,
                on_error=self.recognition_bridge.on_error,
            )
            self.window.set_listening(True)
        except Exception as e:
            logger.exception("Failed to start recognition")
            QMessageBox.critical(self.window, "Error", f"Could not start speech recognition: {e}")

    @Slot()
    def stop_listening(self):
        if self.recognizer:
            self.recognizer.stop()
            self.recognizer = None
        self.window.set_listening(False)

    @Slot()
    def rewind_script(self):
        self.alignment_engine.reset()
        self.window.highlight_word(-1)

    def _on_recognition_result(self, result: RecognitionResult):
        matches = self.alignment_engine.align_words(result.words)
        for match in matches:
            self.window.highlight_word(match.token_index, match.confidence)

    def _on_recognition_error(self, message: str):
        logger.error(f"Recognition Error: {message}")
        self.stop_listening()
        QMessageBox.critical(self.window, "Recognition Error", message)

    @Slot()
    def start_recording(self):
        if self.recording_started_at:
            return

        camera = self.system_profile.camera_by_ffmpeg_name(self.settings.video_device)
        if not camera:
            QMessageBox.critical(self.window, "Error", "No camera selected or found.")
            return

        # Prepare output
        project_dir = self.settings.output_dir
        if not project_dir:
            project_dir = self.window.choose_project_folder()
            if not project_dir:
                return
            self.apply_settings({"output_dir": project_dir})
            self.window.set_recording_directory(project_dir)

        try:
            config = RecordingConfig(
                sample_rate=self.settings.recording_sample_rate,
                bit_depth=self.settings.recording_bit_depth,
                channels=self.settings.recording_channels,
                output_format=self.settings.recording_format
            )
            files = self.file_manager.prepare_session(Path(project_dir), config)
            
            # CRITICAL: Release camera for FFmpeg
            self.preview_controller.stop(wait=True)
            
            self.recording_controller.start(self.settings, camera, files.video_path)
            
            # Also start listening if not already
            self.start_listening()
            
        except Exception as e:
            logger.exception("Failed to start recording")
            QMessageBox.critical(self.window, "Error", f"Failed to start recording: {e}")

    @Slot()
    def stop_recording(self):
        self.recording_controller.stop()

    def _on_recording_started(self):
        self.recording_started_at = time.time()
        self.recording_timer.start(1000)
        self.window.set_recording(True, "Recording...")
        self.window.set_status("Recording in progress...")

    def _on_recording_stopped(self, return_code: int):
        self.recording_started_at = None
        self.recording_timer.stop()
        self.window.set_recording(False)
        self.window.statusBar().showMessage("Recording stopped.")
        
        if return_code == 0:
            self.window.set_status("Recording saved successfully.")
        else:
            self.window.set_status(f"Recording finished with code {return_code}")
            
        # Restart preview
        self._update_preview_state()

    def _on_recording_error(self, message: str):
        logger.error(f"Recording Error: {message}")
        QMessageBox.critical(self.window, "Recording Error", message)
        self._on_recording_stopped(-1)

    def _on_preview_error(self, message: str):
        logger.error(f"Preview Error: {message}")
        self.window.set_status(f"Preview Error: {message}")

    def _update_recording_status(self):
        if self.recording_started_at:
            elapsed = int(time.time() - self.recording_started_at)
            m, s = divmod(elapsed, 60)
            h, m = divmod(m, 60)
            # Use property or method to update timer on UI if available
            try:
                # MainWindow main_controls has no direct timer text setter, but we can emit a status
                self.window.set_status(f"Recording... {h:02d}:{m:02d}:{s:02d}")
            except Exception:
                pass

    def show(self):
        self.window.show()

    def run(self):
        self.show()
