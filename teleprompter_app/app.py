from __future__ import annotations

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
from teleprompter_app.preview import PreviewController
from teleprompter_app.system_probe import probe_system
from teleprompter_app.system_profile import CameraProfile
from teleprompter_app.recording.session_controller import (
    RecordingSessionController,
    MODE_MAP,
    normalize_recording_mode,
)
from teleprompter_app.recording.session_paths import create_session_paths
from teleprompter_app.recording.script_subtitle_generator import ScriptSubtitleGenerator
from teleprompter_app.recording.subtitle_timeline import SubtitleTimeline
from teleprompter_app.utils.config import AppSettings, ConfigManager, SubtitleTimingMode
from teleprompter_app.recording.media_probe import write_ffprobe_json

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
        self.recording_session = RecordingSessionController(ffmpeg_path="ffmpeg")
        self.recording_session.error.connect(self._on_recording_error)
        self.recording_session.stopped.connect(self._on_recording_stopped)
        
        # Hardware Managers
        self.mic_manager = MicrophoneManager()
        
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
        self._restart_preview_after_recording = False
        self._recognition_started_by_recording = False
        self.subtitle_generator: ScriptSubtitleGenerator | None = None
        self.subtitle_timeline: SubtitleTimeline = SubtitleTimeline()
        self.current_script_text: str = ""
        self.current_script_tokens: list = []
        self.current_recording_paths = None
        self.current_recording_mode = None
        self._is_recording = False
        self._preview_restart_pending = False
        
        # Highlighter clock for deterministic progression during recording
        self.highlighter_timer = QTimer(self)
        self.highlighter_timer.timeout.connect(self._advance_highlighter)
        self.current_highlight_index = -1
        
        self._connect_signals()
        
        # Initial state
        self.window.main_controls.populate_preview_cameras(self.system_profile.cameras)
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
        self.window.preview_camera_changed.connect(self._on_preview_camera_changed)
        self.window.config_saved.connect(self._on_config_saved)
        # Config dialog → Controller: keep system_profile in sync after verification
        self.window.profile_updated.connect(self._on_profile_updated)
        
        # Preview -> UI
        self.preview_controller.frame_ready.connect(self.window.preview_overlay.set_frame)
        self.preview_controller.fps_ready.connect(self.window.preview_overlay.set_fps)
        self.preview_controller.error.connect(self._on_preview_error)
        
        # Recording -> UI
        self.recording_session.started.connect(self._on_recording_started)
        self.recording_session.stopped.connect(self._on_recording_stopped)
        self.recording_session.error.connect(self._on_recording_error)
        self.recording_session.performance_warning.connect(self._on_performance_warning)
        
        # Recognition -> Controller/UI
        self.recognition_bridge.result_ready.connect(self._on_recognition_result)
        self.recognition_bridge.status_changed.connect(self.window.set_status)
        self.recognition_bridge.error_occurred.connect(self._on_recognition_error)
        
        # Highlighter -> Timeline
        self.window.teleprompter.word_highlighted.connect(self._on_word_highlighted)
        
        # Manual Navigation
        self.window.next_word_requested.connect(self._advance_to_next_word)
        self.window.prev_word_requested.connect(self._rewind_to_prev_word)
        self.window.next_phrase_requested.connect(self._advance_to_next_phrase)
        self.window.prev_phrase_requested.connect(self._rewind_to_prev_phrase)

    def apply_settings(self, settings_dict: dict):
        self.settings = self.settings.updated(settings_dict)
        self.settings_manager.save(self.settings)
        
        # Update UI components
        self.window.apply_settings(self.settings)
        
        # Update preview if needed
        self._update_preview_state()

    def _update_preview_state(self):
        """Apply current preview mode from settings."""
        mode = getattr(self.settings, "preview_background_mode", "color")
        self._apply_background_mode(mode)

    def _apply_background_mode(self, mode: str) -> None:
        """Central handler for all preview background changes."""
        if mode == "none":
            self._stop_preview_and_clear()
            return

        if mode == "color":
            self._stop_preview_and_clear()
            self.window.preview_overlay.set_background_color(
                self.settings.background_color or "#000000"
            )
            return

        if mode == "camera":
            camera = self._resolve_preview_camera(self.settings)
            if camera is None:
                # No camera selected — fall back to color
                self._stop_preview_and_clear()
                return
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

    def _stop_preview_and_clear(self) -> None:
        """Stop the preview worker and clear the preview frame."""
        self.preview_controller.stop(wait=False)
        self.window.preview_overlay.enable_preview(False)
        self.window.preview_overlay.clear_preview_frame()

    def _on_background_mode_changed(self, mode: str):
        self.settings = self.settings.updated({
            "preview_background_mode": mode,
            "use_camera_background": (mode == "camera"),
        })
        self.settings_manager.save(self.settings)
        self._apply_background_mode(mode)

    def _on_preview_resolution_changed(self, res: str):
        self.apply_settings({"preview_resolution": res})

    def _on_preview_camera_changed(self, cam_name: str):
        self.apply_settings({"preview_video_device": cam_name})

    def _on_config_saved(self):
        # Reload settings after config dialog closes
        self.settings = self.settings_manager.load()
        self.apply_settings({})

    def _on_profile_updated(self, new_profile) -> None:
        """
        Called whenever the config dialog verifies encoders.
        Replaces the controller’s system_profile with the freshly-verified
        version so start_recording() never sees stale UNSUPPORTED states.
        """
        self.system_profile = new_profile
        logger.info(
            "system_profile updated from config dialog — hw encoders: %s",
            [(e.name, e.state.value) for e in new_profile.hardware_encoders()],
        )

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
            self.current_script_text = parsed.plain_text
            self.current_script_tokens = tokenized.tokens
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
        # 1. Highlighting / Alignment
        matches = self.alignment_engine.align_words(result.words)
        for match in matches:
            self.window.highlight_word(match.token_index, match.confidence)

        # 2. Subtitles - SPEECH mode logic
        # In SPEECH timing mode, recognition matches drive the highlighter progression
        # which in turn records into the subtitle timeline.
        pass

    def _on_recognition_error(self, message: str):
        logger.error(f"Recognition Error: {message}")
        self.stop_listening()
        QMessageBox.critical(self.window, "Recognition Error", message)

    def _get_video_extension(self, settings: AppSettings) -> str:
        container = (getattr(settings, "container", "") or "").strip().lower().lstrip(".")
        if container in {"mkv", "mp4", "avi", "mov", "webm"}:
            return container
        return "mkv"

    def _get_audio_extension(self, settings: AppSettings) -> str:
        codec = (getattr(settings, "audio_codec", "") or "").strip().lower()
        mapping = {
            "flac": "flac",
            "libmp3lame": "mp3",
            "mp3": "mp3",
            "pcm_s16le": "wav",
            "aac": "m4a",
            "libopus": "opus",
            "opus": "opus",
        }
        return mapping.get(codec, "flac")

    def _resolve_recording_camera(self, settings: AppSettings):
        selected = (settings.recording_video_device or settings.video_device or "").strip()
        if selected:
            camera = self.system_profile.camera_by_ffmpeg_name(selected)
            if camera:
                return camera
            for cam in self.system_profile.cameras:
                if cam.name == selected or cam.ffmpeg_name == selected:
                    return cam

        if len(self.system_profile.cameras) == 1:
            return self.system_profile.cameras[0]
        return None

    def _resolve_preview_camera(self, settings: AppSettings):
        selected = self.window.main_controls.current_preview_camera()

        # Explicit "no preview" selection
        if selected == "__none__":
            return None

        if selected == "__same_as_recording__":
            selected = (settings.recording_video_device or settings.video_device or "").strip()

        if selected:
            camera = self.system_profile.camera_by_ffmpeg_name(selected)
            if camera:
                return camera
            for cam in self.system_profile.cameras:
                if cam.name == selected or cam.ffmpeg_name == selected:
                    return cam
        return None

    def _same_camera(self, a: CameraProfile | None, b: CameraProfile | None) -> bool:
        if a is None or b is None:
            return False
        return a.ffmpeg_name == b.ffmpeg_name or a.opencv_index == b.opencv_index

    def _cleanup_failed_recording_paths(self, paths):
        if not paths:
            return
        for path in (paths.video_path, paths.audio_path, paths.subtitle_path):
            try:
                if path.exists() and path.stat().st_size == 0:
                    path.unlink()
                    logger.info("Deleted empty failed recording file: %s", path)
            except Exception:
                logger.warning("Could not clean failed output: %s", path, exc_info=True)

    def _pause_preview_for_recording(self, message: str = "") -> None:
        self._restart_preview_after_recording = False
        try:
            if self.preview_controller and self.preview_controller.is_running():
                self._restart_preview_after_recording = True
                self.preview_controller.stop(wait=True)
            self.window.preview_overlay.set_preview_paused(True, message or "Preview paused")
            self.window.preview_overlay.clear_preview_frame()
        except Exception:
            logger.exception("Could not pause preview before video recording")

    def _ensure_recognition_for_recording(self) -> None:
        if not self.vosk_bridge.is_listening():
            self._recognition_started_by_recording = True
            self.start_listening()

    def _on_word_highlighted(self, index: int) -> None:
        if self._is_recording and self.subtitle_timeline:
            self.subtitle_timeline.record_highlight(index)
            self.current_highlight_index = index

    def _advance_highlighter(self) -> None:
        if not self._is_recording:
            return
        
        # Only auto-advance if we are in AUTO mode
        if self.settings.subtitle_timing_mode != SubtitleTimingMode.AUTO:
            self.highlighter_timer.stop()
            return

        self._advance_to_next_word()

    def _advance_to_next_word(self) -> None:
        next_index = self.current_highlight_index + 1
        if next_index < len(self.current_script_tokens):
            self.window.highlight_word(next_index)
        else:
            self.highlighter_timer.stop()

    def _rewind_to_prev_word(self) -> None:
        prev_index = self.current_highlight_index - 1
        if prev_index >= -1:
            self.window.highlight_word(prev_index)

    def _advance_to_next_phrase(self) -> None:
        if not self.subtitle_generator or not self.subtitle_generator.phrases:
            return
        
        # Find the first word of the next phrase
        for phrase_indices in self.subtitle_generator.phrases:
            if phrase_indices[0] > self.current_highlight_index:
                self.window.highlight_word(phrase_indices[0])
                return

    def _rewind_to_prev_phrase(self) -> None:
        if not self.subtitle_generator or not self.subtitle_generator.phrases:
            return
            
        # Find the first word of the previous phrase
        for i in range(len(self.subtitle_generator.phrases) - 1, -1, -1):
            phrase_indices = self.subtitle_generator.phrases[i]
            if phrase_indices[0] < self.current_highlight_index:
                self.window.highlight_word(phrase_indices[0])
                return
        
        # If none found, rewind to start
        self.window.highlight_word(-1)

    @Slot()
    def start_recording(self):
        if not self._validate_recording_settings():
            return

        if self._is_recording:
            return

        paths = None
        try:
            # 1. Force settings mode from live UI data
            live_mode = self.window.main_controls.current_recording_mode()
            self.settings = self.settings.updated({"recording_mode": live_mode})

            logger.info("Start recording requested. live_mode=%r settings.recording_mode=%r", 
                        live_mode, self.settings.recording_mode)

            mode_key = normalize_recording_mode(self.settings.recording_mode)
            mode_spec = MODE_MAP.get(mode_key)
            if mode_spec is None:
                raise RuntimeError(f"Unsupported recording mode: {live_mode!r}")

            logger.info("Recording mode resolved before validation: mode=%r video=%s audio=%s srt=%s",
                        mode_key, mode_spec.video, mode_spec.audio, mode_spec.srt)

            # 2. Resolve recording camera only if video is needed
            recording_camera = None
            if mode_spec.video:
                recording_camera = self._resolve_recording_camera(self.settings)
                if recording_camera is None:
                    raise RuntimeError(
                        f"Video recording requested but no camera is selected. "
                        f"device={self.settings.recording_video_device or self.settings.video_device!r}"
                    )

                if self.settings.video_encoder_type == "hardware":
                    hw_enc = self.settings.hardware_encoder
                    if not hw_enc or hw_enc.lower() == "none":
                        raise RuntimeError("Hardware encoding selected but no hardware encoder is configured.")

                    from teleprompter_app.system_profile import EncoderState
                    from teleprompter_app.recording.encoder_probe import verify_encoder_usable

                    enc_prof = self.system_profile.encoder_by_name(hw_enc)
                    if enc_prof is None:
                        raise RuntimeError(
                            f"Hardware encoder '{hw_enc}' is not known to this system. "
                            "Open Configure and re-select a hardware encoder."
                        )

                    # --------------------------------------------------------
                    # State machine:
                    #   AVAILABLE   → proceed immediately (no re-verify)
                    #   UNSUPPORTED → attempt lazy verification now
                    #   UNAVAILABLE → fail fast (already failed verification)
                    # --------------------------------------------------------
                    if enc_prof.state is EncoderState.UNAVAILABLE:
                        raise RuntimeError(
                            f"Hardware encoder '{hw_enc}' failed verification: {enc_prof.failure_reason}"
                        )

                    if enc_prof.state is EncoderState.UNSUPPORTED:
                        logger.info(
                            "Encoder '%s' is UNSUPPORTED — attempting lazy verification before recording.",
                            hw_enc,
                        )
                        from PySide6.QtWidgets import QApplication
                        from PySide6.QtCore import Qt
                        QApplication.setOverrideCursor(Qt.WaitCursor)
                        try:
                            usable, reason = verify_encoder_usable("ffmpeg", hw_enc)
                            if usable:
                                self.system_profile = self.system_profile.with_encoder_verification(
                                    hw_enc, EncoderState.AVAILABLE, ""
                                )
                                self.system_profile.save_encoder_cache()
                                enc_prof = self.system_profile.encoder_by_name(hw_enc)
                            else:
                                self.system_profile = self.system_profile.with_encoder_verification(
                                    hw_enc, EncoderState.UNAVAILABLE, reason
                                )
                                self.system_profile.save_encoder_cache()
                                raise RuntimeError(
                                    f"Hardware encoder '{hw_enc}' failed verification:\n{reason}"
                                )
                        finally:
                            QApplication.restoreOverrideCursor()

                    # At this point enc_prof.state must be AVAILABLE
                    if enc_prof is None or enc_prof.state is not EncoderState.AVAILABLE:
                        raise RuntimeError(
                            f"Hardware encoder '{hw_enc}' is not verified usable. "
                            "Open Configure to verify it first."
                        )

                    logger.info("Hardware encoder '%s' is AVAILABLE — proceeding.", hw_enc)

            # Resolve preview camera for conflict check
            preview_camera = self._resolve_preview_camera(self.settings)

            # 3. Handle Preview Conflict
            if mode_spec.video:
                if self._same_camera(recording_camera, preview_camera):
                    self._pause_preview_for_recording("Preview paused: Recording uses the same camera")
                else:
                    # If preview is enabled, ensure it's running on its own camera
                    self._update_preview_state()
            
            # 4. Validate audio only if audio is needed
            if mode_spec.audio and not self.settings.audio_device:
                raise RuntimeError("Audio recording requested but no microphone is selected.")

            # 4. Prepare directory
            project_dir = self.settings.output_dir
            if not project_dir:
                project_dir = self.window.choose_project_folder()
                if not project_dir:
                    return
                self.apply_settings({"output_dir": project_dir})
                self.window.set_recording_directory(project_dir)

            output_root = Path(project_dir).expanduser().resolve()
            output_root.mkdir(parents=True, exist_ok=True)

            # 5. Create paths after validation
            paths = create_session_paths(
                output_root,
                video_ext=self._get_video_extension(self.settings),
                audio_ext=self._get_audio_extension(self.settings),
            )

            # 6. Store state
            self.current_recording_paths = paths
            self.current_recording_mode = mode_spec
            
            self._is_recording = True

            # 7. Start recognition if needed for highlighting
            if mode_spec.srt and self.settings.subtitle_timing_mode == SubtitleTimingMode.SPEECH:
                self._ensure_recognition_for_recording()

            # 8. Initialize script subtitle generator & timeline
            if mode_spec.srt:
                self.subtitle_generator = ScriptSubtitleGenerator(
                    self.current_script_text,
                    tokens=self.current_script_tokens
                )
                self.subtitle_timeline.start()
                self.current_highlight_index = -1 # Start from beginning
                
                # Always record an initial anchor at T=0
                start_idx = max(0, self.window.teleprompter.current_index)
                self.window.highlight_word(start_idx)
                self.subtitle_timeline.record_highlight(start_idx)
                
                # If in AUTO mode, start the highlighter clock
                if self.settings.subtitle_timing_mode == SubtitleTimingMode.AUTO:
                    wpm = self.settings.words_per_minute or 150
                    interval_ms = int(60000 / wpm)
                    self.highlighter_timer.start(interval_ms)
                
                logger.info("Script subtitle generator & timeline started (Mode: %s).", self.settings.subtitle_timing_mode)

            # 9. Start session
            self.recording_session.start(
                settings=self.settings,
                camera=recording_camera,
                paths=paths,
            )
            
            self.window.main_controls.set_recording_state(True)
            self.window.set_status(f"Recording session {paths.session_id} in progress...")

        except Exception as e:
            logger.exception("Failed to start recording")
            self._is_recording = False
            self.window.set_recording(False)
            
            if self.subtitle_generator:
                try:
                    self.subtitle_generator.stop()
                except Exception:
                    pass
                self.subtitle_generator = None
            
            if paths:
                self._cleanup_failed_recording_paths(paths)

            QMessageBox.critical(self.window, "Recording Error", str(e))

    @Slot()
    def stop_recording(self):
        if not self._is_recording:
            return
        try:
            if self.recording_session:
                self.recording_session.stop()

            if self.subtitle_generator:
                try:
                    self.highlighter_timer.stop()
                    duration = time.time() - self.recording_started_at if self.recording_started_at else None
                    # Write both SRT versions simultaneously using captured timeline
                    self.subtitle_generator.write_all(
                        self.current_recording_paths.subtitle_path,
                        timeline=self.subtitle_timeline,
                        mode=self.settings.subtitle_mode,
                        max_duration=duration
                    )
                finally:
                    self.subtitle_generator = None
                    self.subtitle_timeline = SubtitleTimeline()

            if self._recognition_started_by_recording:
                self.stop_listening()
                self._recognition_started_by_recording = False

            self.recording_timer.stop()
            self._is_recording = False
            self.window.set_recording(False)
        except Exception as e:
            logger.exception("Failed to stop recording")
            self._is_recording = False
            self.window.set_recording(False)
            QMessageBox.critical(self.window, "Recording Error", str(e))

    def _on_recording_started(self):
        self.recording_started_at = time.time()
        self.recording_timer.start(1000)
        self.window.set_recording(True, "Recording...")
        self.window.set_status("Recording in progress...")

    def _on_recording_stopped(self, return_code: int | None = None):
        if not self._is_recording:
            logger.debug("Ignoring duplicate recording stopped event")
            return

        logger.info("Recording stopped. return_code=%r", return_code)
        self._is_recording = False
        self.recording_started_at = None
        self.recording_timer.stop()
        
        if self.subtitle_generator:
            try:
                self.highlighter_timer.stop()
                # Use cached started_at if still available, or just save full script
                duration = time.time() - self.recording_started_at if self.recording_started_at else None
                self.subtitle_generator.write_all(
                    self.current_recording_paths.subtitle_path,
                    timeline=self.subtitle_timeline,
                    mode=self.settings.subtitle_mode,
                    max_duration=duration
                )
            finally:
                self.subtitle_generator = None
                self.subtitle_timeline = SubtitleTimeline()

        self.window.set_recording(False)
        self.window.statusBar().showMessage("Recording stopped.")
        self.window.preview_overlay.set_preview_paused(False)
        
        summary = "Recording complete:\n"
        if self.current_recording_paths:
            for path in [self.current_recording_paths.video_path, self.current_recording_paths.audio_path]:
                if path.exists():
                    write_ffprobe_json(path)
                    summary += f"- {path.name} ({path.stat().st_size / 1024 / 1024:.1f} MB)\n"
            if self.current_recording_paths.subtitle_path.exists():
                summary += f"- {self.current_recording_paths.subtitle_path.name}\n"

        if return_code is None or return_code == 0:
            self.window.set_status("Recording saved successfully.")
            QMessageBox.information(self.window, "Recording Complete", summary)
        else:
            self.window.set_status(f"Recording finished with code {return_code}")
            
        # Restart preview only if background mode is camera and preview was running before
        current_mode = getattr(self.settings, "preview_background_mode", "color")
        if current_mode == "none":
            # User chose No Preview — do not restart
            logger.debug("Preview not restarted: background mode is 'none'")
        elif getattr(self, "_restart_preview_after_recording", False):
            self._schedule_preview_restart()

    def _schedule_preview_restart(self) -> None:
        if self._preview_restart_pending:
            return
        self._preview_restart_pending = True
        QTimer.singleShot(750, self._restart_preview_after_recording_safe)

    def _restart_preview_after_recording_safe(self) -> None:
        self._preview_restart_pending = False
        if self.preview_controller and self.preview_controller.is_running():
            return
        
        # Resolve latest preview camera and restart
        self._update_preview_state()

    def _on_recording_error(self, message: str):
        logger.error(f"Recording Error: {message}")
        QMessageBox.critical(self.window, "Recording Error", message)
        self._on_recording_stopped(-1)

    def _on_preview_error(self, message: str):
        logger.error(f"Preview Error: {message}")
        self.window.set_status(f"Preview Error: {message}")

    def _on_performance_warning(self, message: str):
        now = time.time()
        if now - getattr(self, "_last_perf_warning_time", 0) > 10:
            logger.warning(f"Performance Warning: {message}")
            self.window.statusBar().showMessage(f"⚠ {message}", 5000)
            self._last_perf_warning_time = now

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

    def shutdown(self):
        logger.info("Application shutdown started")
        try:
            if self.recording_session:
                self.recording_session.stop()
        except Exception:
            logger.exception("Could not stop recording session during shutdown")

        try:
            if self.subtitle_generator:
                # If shutdown happens during recording, we still try to save what we can
                if self.current_recording_paths:
                    self.highlighter_timer.stop()
                    duration = time.time() - self.recording_started_at if self.recording_started_at else None
                    self.subtitle_generator.write_all(
                        self.current_recording_paths.subtitle_path,
                        timeline=self.subtitle_timeline,
                        mode=self.settings.subtitle_mode,
                        max_duration=duration
                    )
                self.subtitle_generator = None
                self.subtitle_timeline = SubtitleTimeline()
        except Exception:
            logger.exception("Could not stop subtitle generator during shutdown")

        try:
            if self.preview_controller:
                self.preview_controller.stop(wait=True)
        except Exception:
            logger.exception("Could not stop preview during shutdown")

        try:
            self.stop_listening()
        except Exception:
            logger.exception("Could not stop recognition during shutdown")

        logger.info("Application shutdown complete")

    def _validate_recording_settings(self) -> bool:
        mode_key = normalize_recording_mode(self.settings.recording_mode)
        mode_spec = MODE_MAP.get(mode_key)
        if not mode_spec:
            return False

        # If SRT only, we just need a script
        if mode_spec.srt and not mode_spec.video and not mode_spec.audio:
            if not self.current_script_text:
                QMessageBox.critical(self.window, "No Script", "Please load a script before recording subtitles.")
                return False
            return True

        # Standard A/V checks
        if mode_spec.video and not self.settings.recording_video_device:
            QMessageBox.critical(self.window, "No Camera", "Please select a camera in Configure.")
            return False
        if mode_spec.audio and not self.settings.audio_device:
            QMessageBox.critical(self.window, "No Microphone", "Please select a microphone in Configure.")
            return False

        risk = self._encoding_risk_level(self.settings)
        if risk != "high":
            return True

        msg = QMessageBox(self.window)
        msg.setWindowTitle("High Performance Risk")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText("The selected encoding mode may not keep up in real-time at this resolution/FPS.")
        msg.setInformativeText(
            f"Target: {self.settings.resolution} @ {self.settings.fps} FPS\n"
            f"Encoder: {self.settings.software_encoder}\n\n"
            "This can produce frozen, stuttered, or incomplete video files."
        )
        
        copy_btn = msg.addButton("Use Camera Stream Copy", QMessageBox.ButtonRole.ActionRole)
        hw_btn = None
        best_hw = self._best_hardware_encoder()
        if best_hw:
            hw_btn = msg.addButton("Use Best Hardware Encoder", QMessageBox.ButtonRole.ActionRole)
        
        continue_btn = msg.addButton("Continue Anyway", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton(QMessageBox.StandardButton.Cancel)
        
        msg.exec()
        clicked = msg.clickedButton()
        
        if clicked == cancel_btn:
            return False
        elif clicked == copy_btn:
            self.settings = self.settings.updated({
                "video_encoder_type": "copy",
                "video_codec_mode": "copy"
            })
            ConfigManager().save(self.settings)
            return True
        elif hw_btn and clicked == hw_btn:
            self.settings = self.settings.updated({
                "video_encoder_type": "hardware",
                "hardware_encoder": best_hw,
                "video_codec_mode": best_hw
            })
            ConfigManager().save(self.settings)
            return True
        
        return True

    def _encoding_risk_level(self, settings: AppSettings) -> str:
        if settings.video_encoder_type in ("copy", "hardware"):
            return "safe"

        try:
            w, h = map(int, settings.resolution.split("x"))
            fps = float(settings.fps)
            pixels_per_second = w * h * fps
        except (ValueError, TypeError):
            return "medium"

        codec = settings.software_encoder or settings.video_codec_mode or ""

        # Any lossless mode at 1080p60+ is high risk
        if codec in ("libx264_lossless", "lossless_h264", "ffv1", "lossless_ffv1"):
            if pixels_per_second >= 1920 * 1080 * 25:
                return "high"

        # High quality or standard at 4K25+ is high risk on low-end CPUs
        if pixels_per_second >= 3840 * 2160 * 25:
            return "high"

        return "medium"

    def _best_hardware_encoder(self) -> str | None:
        """Return the name of the best verified (AVAILABLE) hardware encoder."""
        from teleprompter_app.system_profile import EncoderState
        best = self.system_profile.best_hardware_encoder()
        if best and best.state == EncoderState.AVAILABLE:
            return best.name

        # If no AVAILABLE hardware encoder, see if there are any UNSUPPORTED ones we can verify
        unsupported = [e for e in self.system_profile.video_encoders if e.kind == "hardware" and e.state == EncoderState.UNSUPPORTED]
        if unsupported:
            logger.info("No verified hardware encoder found; attempting lazy verification on unverified encoders.")
            from teleprompter_app.recording.encoder_probe import verify_encoder_usable
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import Qt
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                for enc in unsupported:
                    usable, reason = verify_encoder_usable("ffmpeg", enc.name)
                    if usable:
                        self.system_profile = self.system_profile.with_encoder_verification(enc.name, EncoderState.AVAILABLE, "")
                        self.system_profile.save_encoder_cache()
                        return enc.name
                    else:
                        self.system_profile = self.system_profile.with_encoder_verification(enc.name, EncoderState.UNAVAILABLE, reason)
                        self.system_profile.save_encoder_cache()
            finally:
                QApplication.restoreOverrideCursor()
        return None

    def show(self):
        self.window.show()

    def run(self):
        self.show()
