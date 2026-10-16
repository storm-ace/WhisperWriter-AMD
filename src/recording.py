"""
Dictation flow: start/stop recordings from the hotkey, the record button or the pill,
type the results into the right window, and report them for storage.
"""

import os

from PySide6.QtCore import QObject, Signal

from active_window import Target, backtick_filenames, foreground_target
from engine import EngineState
from result_thread import ResultThread, Status
from utils import ConfigManager


class Source:
    HOTKEY = 'hotkey'     # types into the window that had focus
    BUTTON = 'button'     # the app's own record button: dictation only goes into the session


class DictationController(QObject):
    """
    Owns the recording threads. Only the newest recording drives the UI; an older one that
    is still transcribing its last words keeps running in the background and its results
    are still typed and saved.
    """

    dictated = Signal(object, object, bool)   # TranscriptionResult, Target, typed
    statusChanged = Signal(str)               # Status of the current recording
    levelChanged = Signal(float)

    def __init__(self, engine, overlay, input_simulator):
        super().__init__()
        self.engine = engine
        self.overlay = overlay
        self.input_simulator = input_simulator
        self.current = None
        self._threads = set()

    # ------------------------------------------------------------------ commands
    @property
    def recording(self):
        return bool(self.current and self.current.is_recording)

    @staticmethod
    def _mode():
        return ConfigManager.get_config_value('recording_options', 'recording_mode') or 'continuous'

    def on_hotkey_pressed(self, suppress_enter=False):
        if self.recording:
            if self._mode() != 'hold_to_record':
                self.stop()
            return
        self.start(Source.HOTKEY, suppress_enter)

    def on_hotkey_released(self):
        if self._mode() == 'hold_to_record' and self.recording:
            self.stop()

    def toggle_from_button(self):
        if self.recording:
            self.stop()
        else:
            self.start(Source.BUTTON, suppress_enter=True)

    def start(self, source, suppress_enter=False):
        if self.recording:
            return
        if not self.engine.ready:
            msg = ('The engine is still starting — try again in a moment.'
                   if self.engine.status.state is EngineState.STARTING else
                   'The engine is not running. Open Settings → Engine & GPU to fix it.')
            self.overlay.show_message(msg, head='Not ready')
            return
        thread = ResultThread(self.engine.local_model, continuous=self._mode() == 'continuous')
        thread.source = source
        thread.target = foreground_target() if source == Source.HOTKEY else Target()
        thread.suppress_enter = suppress_enter
        thread.statusSignal.connect(lambda status, t=thread: self._on_status(t, status))
        thread.resultSignal.connect(lambda result, t=thread: self._on_result(t, result))
        thread.levelSignal.connect(lambda level, t=thread: t is self.current and self.levelChanged.emit(level))
        thread.finished.connect(lambda t=thread: self._threads.discard(t))
        self._threads.add(thread)
        self.current = thread
        thread.start()

    def stop(self):
        """Stop listening; what was said so far is still transcribed and typed."""
        if self.current:
            self.current.stop_recording()

    def cancel(self):
        """Stop listening and drop anything not typed yet."""
        if self.current:
            self.current.cancel()
        self.overlay.hide()

    def shutdown(self):
        for thread in list(self._threads):
            thread.cancel()
        for thread in list(self._threads):
            thread.wait(3000)

    # ------------------------------------------------------------------ thread events
    @staticmethod
    def _overlay_enabled():
        return not ConfigManager.get_config_value('misc', 'hide_status_window')

    def _on_status(self, thread, status):
        if thread is not self.current:
            return
        self.statusChanged.emit(status)
        if not self._overlay_enabled():
            return
        if status == Status.RECORDING:
            self.overlay.show_recording(thread.target.label if thread.source == Source.HOTKEY else 'session')
        elif status == Status.TRANSCRIBING:
            self.overlay.show_transcribing(self.engine.status.compute_label)
        elif status == Status.IDLE and not self.overlay.is_showing_result:
            self.overlay.hide()

    def _on_result(self, thread, result):
        if result.error:
            self.overlay.show_message(result.error, head='Transcription failed')
            return
        if not result.text.strip():
            return   # noise or a filtered hallucination

        target = thread.target
        claude_mode = (ConfigManager.get_config_value('claude_code', 'smart_terminal_mode')
                       and target.is_claude_code)
        if claude_mode and ConfigManager.get_config_value('claude_code', 'backtick_filenames'):
            result.text = backtick_filenames(result.text)

        typed = thread.source == Source.HOTKEY and bool(target.exe) and not target.is_own_process
        if typed:
            self.input_simulator.typewrite(result.text)
            press_enter = (ConfigManager.get_config_value('post_processing', 'press_enter')
                           and not thread.suppress_enter
                           and not (claude_mode and ConfigManager.get_config_value('claude_code', 'suppress_enter')))
            if press_enter:
                self.input_simulator.press_enter()

        self.dictated.emit(result, target, typed)

        if self._overlay_enabled():
            head = (f'Typed into {target.app} · {result.latency_s:.1f} s' if typed
                    else f'Saved to session · {result.latency_s:.1f} s')
            self.overlay.show_result(result.text, head, 'Ctrl Z to undo' if typed else '',
                                     then_recording=thread is self.current and thread.is_recording)
        if ConfigManager.get_config_value('misc', 'noise_on_completion'):
            from audioplayer import AudioPlayer   # optional dependency, only when enabled
            AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=False)
