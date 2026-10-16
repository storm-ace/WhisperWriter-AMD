import os
import sys
import threading
import time

# Run from the repository root so relative asset paths resolve, wherever we were launched.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QCursor, QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import claude_integration
from active_window import Target, backtick_filenames, foreground_target
from sessions import SessionStore, default_db_path
from ui import theme
from ui.main_window import MainWindow
from ui.overlay import RecorderOverlay
from utils import ConfigManager

INSTANCE_NAME = 'WhisperWriter-AMD'


class EngineManager(QObject):
    """Starts the transcription engine (and optional LLM server) off the UI thread."""

    statusChanged = Signal(str, str, str)   # state (starting|ready|error), title, detail

    def __init__(self):
        super().__init__()
        self.engine = None
        self.local_model = None
        self.whispercpp_server = None
        self.llama_server = None
        self.state = 'starting'
        self._generation = 0
        self._lock = threading.Lock()

    @property
    def ready(self):
        return self.state == 'ready'

    def start(self):
        self.stop()
        self._generation += 1
        gen = self._generation
        self._emit(gen, 'starting', 'Starting engine…', '')
        threading.Thread(target=self._start_blocking, args=(gen,), daemon=True).start()

    def _emit(self, gen, state, title, detail):
        if gen == self._generation:
            self.state = state
            self.statusChanged.emit(state, title, detail)

    def _start_blocking(self, gen):
        from transcription import create_local_model, resolve_engine
        engine = resolve_engine()
        self.engine = engine
        opts = ConfigManager.get_config_section('model_options')
        try:
            if engine == 'whispercpp':
                from whispercpp_server import WhisperCppServer
                wc = opts.get('whispercpp', {})
                model = os.path.basename(wc.get('model_path') or '').replace('ggml-', '').replace('.bin', '')
                self._emit(gen, 'starting', f'Loading {model or "model"} on GPU…', '')
                server = WhisperCppServer()
                with self._lock:
                    self.whispercpp_server = server
                ok = server.start() if wc.get('auto_start', True) else server.is_available()
                if gen != self._generation:
                    return
                if not ok:
                    self._emit(gen, 'error', 'whisper.cpp server not running',
                               'Check the server binary and ggml model path in Settings → Engine.')
                    return
                title = f'Ready · whisper.cpp · Vulkan · {model}' if model else 'Ready · whisper.cpp · Vulkan'
                detail = f'whisper-server at {server.host}:{server.port}'
            else:
                local = opts.get('local', {})
                self._emit(gen, 'starting', f'Loading {local.get("model")} on CPU…', '')
                model = create_local_model()
                if gen != self._generation:
                    return
                self.local_model = model
                title = f'Ready · faster-whisper · {local.get("device", "cpu").upper()} · {local.get("model")}'
                detail = ''

            llm = ConfigManager.get_config_section('post_processing').get('llm_correction', {})
            if llm.get('enabled') and llm.get('auto_start', True):
                from llamacpp_server import LlamaCppServer
                server = LlamaCppServer()
                with self._lock:
                    self.llama_server = server
                if server.start():
                    detail = (detail + ' · ' if detail else '') + 'LLM correction ready'
                else:
                    detail = (detail + ' · ' if detail else '') + 'LLM server failed (raw text is used)'
            self._emit(gen, 'ready', title, detail)
        except Exception as e:
            self._emit(gen, 'error', 'Engine failed to start', str(e))

    def stop(self):
        with self._lock:
            servers = [self.whispercpp_server, self.llama_server]
            self.whispercpp_server = self.llama_server = None
        for server in servers:
            if server:
                server.stop()
        self.local_model = None


class KeyBridge(QObject):
    """Moves hotkey callbacks from the listener thread onto the Qt main thread."""
    activated = Signal()
    deactivated = Signal()


class WhisperWriterApp(QObject):
    def __init__(self):
        super().__init__()
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        self.app = QApplication(sys.argv)
        self.app.setApplicationName('WhisperWriter')
        self.app.setQuitOnLastWindowClosed(False)   # tray app: closing the window keeps it running

        if not self._claim_single_instance():
            sys.exit(0)

        ConfigManager.initialize()
        first_run = not ConfigManager.config_file_exists()
        theme.apply_app_theme(self.app)
        self.app.setWindowIcon(theme.app_icon())

        self.store = SessionStore(ConfigManager.get_config_value('sessions', 'db_path') or None)
        self.active_session_id = self.store.resolve_active_session(self._gap_minutes())
        self.store.prune_empty_sessions(keep_id=self.active_session_id)

        self.result_thread = None
        self.source = 'hotkey'
        self.target = Target()
        self.suppress_enter_current = False

        self.engine = EngineManager()
        self.engine.statusChanged.connect(self.on_engine_status)

        self.main_window = MainWindow(self.store, claude_integration.is_registered)
        self.main_window.recordClicked.connect(self.on_record_clicked)
        self.main_window.newSessionRequested.connect(self.new_session)
        self.main_window.continueSessionRequested.connect(self.set_active_session)
        self.main_window.settingsApplied.connect(self.apply_settings)
        self.main_window.mcpSetupRequested.connect(self.setup_mcp)
        self.main_window.settings_page.restartEngineRequested.connect(self.engine.start)
        self.main_window.set_active_session(self.active_session_id)

        self.overlay = RecorderOverlay()
        self.overlay.stopRequested.connect(self.stop_recording)
        self.overlay.cancelRequested.connect(self.cancel_recording)

        from input_simulation import InputSimulator
        from key_listener import KeyListener
        self.input_simulator = InputSimulator()
        self.bridge = KeyBridge()
        self.bridge.activated.connect(self.on_activation, Qt.ConnectionType.QueuedConnection)
        self.bridge.deactivated.connect(self.on_deactivation, Qt.ConnectionType.QueuedConnection)
        self.key_listener = KeyListener()
        self.key_listener.add_callback('on_activate', self.bridge.activated.emit)
        self.key_listener.add_callback('on_deactivate', self.bridge.deactivated.emit)
        self.key_listener.start()

        self.create_tray_icon()
        self.engine.start()

        if first_run:
            self.main_window.show()
            self.main_window.show_settings('engine')
            self.main_window.toast.show_message('Welcome! Pick your engine and hit Save & apply.', 6000)
        elif not ConfigManager.get_config_value('misc', 'start_minimized'):
            self.main_window.show()

    # ================================================================ lifecycle
    def _claim_single_instance(self):
        """Hand off to an already running instance (it shows its window) or become it."""
        socket = QLocalSocket()
        socket.connectToServer(INSTANCE_NAME)
        if socket.waitForConnected(300):
            socket.write(b'show')
            socket.flush()
            socket.waitForBytesWritten(300)
            print('WhisperWriter is already running; showing its window.')
            return False
        QLocalServer.removeServer(INSTANCE_NAME)
        self.instance_server = QLocalServer(self)
        self.instance_server.newConnection.connect(self._on_second_instance)
        self.instance_server.listen(INSTANCE_NAME)
        return True

    def _on_second_instance(self):
        conn = self.instance_server.nextPendingConnection()
        if conn:
            conn.disconnectFromServer()
        if hasattr(self, 'main_window'):
            self.main_window.bring_to_front()

    def cleanup(self):
        if getattr(self, 'result_thread', None) and self.result_thread.isRunning():
            self.result_thread.stop()
        if getattr(self, 'key_listener', None):
            self.key_listener.stop()
        if getattr(self, 'input_simulator', None):
            self.input_simulator.cleanup()
        if getattr(self, 'engine', None):
            self.engine.stop()
        if getattr(self, 'store', None):
            self.store.close()

    def exit_app(self):
        self.tray_icon.hide()
        self.cleanup()
        QApplication.quit()

    def run(self):
        sys.exit(self.app.exec())

    # ================================================================ tray
    def create_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(theme.app_icon(), self.app)
        menu = QMenu()
        self.tray_status = QAction('Starting…', self.app)
        self.tray_status.setEnabled(False)
        menu.addAction(self.tray_status)
        menu.addSeparator()
        menu.addAction('Open WhisperWriter', self.main_window.bring_to_front)
        menu.addAction('New session', self.new_session)
        menu.addAction('Search…', self.main_window.open_palette)
        menu.addAction('Settings', self.main_window.show_settings)
        menu.addSeparator()
        menu.addAction('Quit (stops GPU servers)', self.exit_app)
        self.tray_menu = menu
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.setToolTip('WhisperWriter')
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.main_window.isVisible() and self.main_window.isActiveWindow():
                self.main_window.hide()
            else:
                self.main_window.bring_to_front()

    # ================================================================ engine
    def on_engine_status(self, state, title, detail):
        self.main_window.set_engine_status(state, title, None)
        self.main_window.settings_page.set_engine_info(state, title, detail)
        self.tray_status.setText(title)
        self.tray_icon.setToolTip(f'WhisperWriter — {title}')
        if state == 'error':
            self.overlay.show_message(detail or title, head=title)

    def apply_settings(self, old):
        """Apply a saved config in-process, restarting only what changed."""
        new = ConfigManager.snapshot()

        def changed(*keys):
            a, b = old, new
            for k in keys:
                a = a.get(k, {}) if isinstance(a, dict) else None
                b = b.get(k, {}) if isinstance(b, dict) else None
            return a != b

        llm_server_keys = ('enabled', 'binary_path', 'model_path', 'lib_path', 'host', 'port',
                           'n_gpu_layers', 'context_size', 'n_threads', 'auto_start')
        if (changed('model_options', 'engine') or changed('model_options', 'local')
                or changed('model_options', 'whispercpp')
                or any(changed('post_processing', 'llm_correction', k) for k in llm_server_keys)):
            self.engine.start()

        if (changed('recording_options', 'activation_key')
                or changed('recording_options', 'activation_key_no_enter')):
            self.key_listener.update_activation_keys()
        if changed('recording_options', 'input_backend'):
            self.key_listener.stop()
            self.key_listener.update_backend()
            self.key_listener.start()

        if changed('post_processing', 'input_method'):
            from input_simulation import InputSimulator
            self.input_simulator.cleanup()
            self.input_simulator = InputSimulator()

        if changed('sessions', 'db_path'):
            self.store.close()
            self.store = SessionStore(new['sessions'].get('db_path') or None)
            self.main_window.store = self.store
            self.active_session_id = self.store.resolve_active_session(self._gap_minutes())
            self.main_window.set_active_session(self.active_session_id)

        self.main_window.refresh_config_views()
        self.main_window.settings_page.refresh_mcp()

    def setup_mcp(self):
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            ok, message = claude_integration.register(
                ConfigManager.get_config_value('sessions', 'db_path'))
        finally:
            QApplication.restoreOverrideCursor()
        self.main_window.settings_page.refresh_mcp(message)
        self.main_window.refresh_mcp_status()
        self.main_window.toast.show_message(message if ok else 'Could not register — see Settings → Claude Code')

    # ================================================================ sessions
    def _gap_minutes(self):
        return ConfigManager.get_config_value('sessions', 'new_session_after_minutes') or 0

    def set_active_session(self, session_id):
        self.active_session_id = session_id
        self.store.touch_session(session_id)
        self.main_window.set_active_session(session_id)

    def new_session(self):
        current = self.store.get_session(self.active_session_id) if self.active_session_id else None
        if current and current['dictation_count'] == 0:
            self.main_window.set_active_session(current['id'])
        else:
            self.active_session_id = self.store.create_session()
            self.main_window.set_active_session(self.active_session_id)
        self.main_window.bring_to_front()
        self.main_window.show_sessions()

    def _session_for_new_dictation(self):
        session = self.store.get_session(self.active_session_id) if self.active_session_id else None
        gap = self._gap_minutes()
        if (session is None or (gap and session['dictation_count'] > 0
                                and time.time() - session['updated_at'] > gap * 60)):
            self.active_session_id = self.store.create_session()
            self.main_window.active_session_id = self.active_session_id
            self.main_window.show_session(self.active_session_id)
        return self.active_session_id

    # ================================================================ recording
    def on_activation(self):
        self.suppress_enter_current = self.key_listener.suppress_enter
        if self.result_thread and self.result_thread.isRunning():
            mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
            if mode == 'press_to_toggle':
                self.result_thread.stop_recording()
            elif mode == 'continuous':
                self.stop_result_thread()
            return
        self.start_result_thread('hotkey')

    def on_deactivation(self):
        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'hold_to_record':
            if self.result_thread and self.result_thread.isRunning():
                self.result_thread.stop_recording()

    def on_record_clicked(self):
        if self.result_thread and self.result_thread.isRunning():
            if self.result_thread.is_recording:
                self.result_thread.stop_recording()
            return
        self.suppress_enter_current = True
        self.start_result_thread('button')

    def start_result_thread(self, source):
        if self.result_thread and self.result_thread.isRunning():
            return
        if not self.engine.ready:
            msg = ('The engine is still starting — try again in a moment.'
                   if self.engine.state == 'starting' else
                   'The engine is not running. Open Settings → Engine to fix it.')
            self.overlay.show_message(msg, head='Not ready')
            return
        from result_thread import ResultThread
        self.source = source
        # Hotkey: type into whatever window had focus. Record button: our own window has
        # focus, so the dictation only goes into the session.
        self.target = foreground_target() if source == 'hotkey' else Target()
        self.result_thread = ResultThread(self.engine.local_model)
        self.result_thread.statusSignal.connect(self.on_status)
        self.result_thread.levelSignal.connect(self.overlay.push_level)
        self.result_thread.levelSignal.connect(self.main_window.push_level)
        self.result_thread.resultSignal.connect(self.on_result)
        self.result_thread.start()

    def stop_recording(self):
        if self.result_thread and self.result_thread.isRunning():
            self.result_thread.stop_recording()

    def cancel_recording(self):
        self.stop_result_thread()
        self.overlay.hide()

    def stop_result_thread(self):
        if self.result_thread and self.result_thread.isRunning():
            self.result_thread.stop()

    def _overlay_enabled(self):
        return not ConfigManager.get_config_value('misc', 'hide_status_window')

    def on_status(self, status):
        self.main_window.set_recording_state(status)
        if not self._overlay_enabled():
            return
        if status == 'recording':
            self.overlay.show_recording(self.target.label if self.source == 'hotkey' else 'session')
        elif status == 'transcribing':
            self.overlay.show_transcribing('GPU · Vulkan' if self.engine.engine == 'whispercpp' else 'CPU')
        elif status == 'idle' and not (self.result_thread and self.result_thread.isRunning()):
            self.overlay.hide()

    def on_result(self, result):
        text = (result.get('text') or '')
        if result.get('error'):
            # No auto-restart in continuous mode: a broken engine would loop forever.
            self.overlay.show_message(result['error'], head='Transcription failed')
            return
        if not text.strip():
            # Nothing usable (noise or a filtered hallucination): keep listening quietly.
            continuous = ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous'
            if not continuous:
                self.overlay.hide()
            self._after_result()
            return

        target = self.target
        claude_mode = (ConfigManager.get_config_value('claude_code', 'smart_terminal_mode')
                       and target.is_claude_code)
        if claude_mode and ConfigManager.get_config_value('claude_code', 'backtick_filenames'):
            text = backtick_filenames(text)

        typed = self.source == 'hotkey' and target.exe and not target.is_own_process
        if typed:
            self.input_simulator.typewrite(text)
            press_enter = (ConfigManager.get_config_value('post_processing', 'press_enter')
                           and not self.suppress_enter_current
                           and not (claude_mode and ConfigManager.get_config_value('claude_code', 'suppress_enter')))
            if press_enter:
                self.input_simulator.press_enter()

        if ConfigManager.get_config_value('sessions', 'enabled'):
            session_id = self._session_for_new_dictation()
            dictation_id = self.store.add_dictation(
                session_id, text, raw_text=result.get('raw'), duration_s=result.get('duration_s'),
                latency_s=result.get('latency_s'),
                target_app=target.label if typed else None, target_title=target.title if typed else None,
                engine=self.engine.engine, corrected=result.get('corrected'))
            dictation = next((d for d in self.store.dictations(session_id, limit=1)
                              if d['id'] == dictation_id), None)
            if dictation:
                self.main_window.add_dictation(session_id, dictation)
        if typed:
            self.main_window.set_last_target(target.label)

        if self._overlay_enabled():
            latency = result.get('latency_s') or 0
            head = f'Typed into {target.app} · {latency:.1f} s' if typed else f'Saved to session · {latency:.1f} s'
            self.overlay.show_result(text, head, 'Ctrl Z to undo' if typed else '')

        if ConfigManager.get_config_value('misc', 'noise_on_completion'):
            from audioplayer import AudioPlayer
            AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=False)
        self._after_result()

    def _after_result(self):
        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous':
            # Let the finished thread wind down before starting the next recording.
            if self.result_thread:
                self.result_thread.wait(2000)
            self.start_result_thread(self.source)


if __name__ == '__main__':
    app = WhisperWriterApp()
    app.run()
