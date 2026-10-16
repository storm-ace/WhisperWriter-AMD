"""WhisperWriter entry point: app lifecycle, tray, sessions and settings wiring."""

import os
import sys
import threading
import time
import traceback

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import claude_integration
from active_window import find_claude_code_window, focus_window
from engine import EngineManager, EngineState
from input_simulation import InputSimulator
from key_listener import KeyListener
from llamacpp_server import SERVER_KEYS as LLM_SERVER_KEYS
from recording import DictationController
from sessions import SessionStore, app_data_dir
from transcription import summarize_with_llm
from ui import theme
from ui.main_window import MainWindow
from ui.overlay import RecorderOverlay
from utils import APP_ROOT, IS_FROZEN, ConfigManager
from whispercpp_server import BUNDLED_SERVER
INSTANCE_NAME = 'WhisperWriter-AMD'
APP_USER_MODEL_ID = 'Stormace.WhisperWriter.AMD'


def set_windows_app_id():
    """Give the process its own taskbar identity, so Windows shows the WhisperWriter icon
    (and groups its windows) instead of the generic python.exe icon."""
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass


def install_crash_log():
    """Write unhandled exceptions to %APPDATA%/WhisperWriter/crash.log; under pythonw.exe
    (start_hidden.vbs) there is no console that would show them."""
    def hook(exc_type, exc, tb):
        try:
            os.makedirs(app_data_dir(), exist_ok=True)
            with open(os.path.join(app_data_dir(), 'crash.log'), 'a', encoding='utf-8') as f:
                f.write(time.strftime('\n=== %Y-%m-%d %H:%M:%S ===\n'))
                traceback.print_exception(exc_type, exc, tb, file=f)
        except OSError:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook
    threading.excepthook = lambda args: hook(args.exc_type, args.exc_value, args.exc_traceback)


class KeyBridge(QObject):
    """Moves hotkey callbacks from the listener thread onto the Qt main thread."""
    activated = Signal()
    deactivated = Signal()


class BackgroundTasks(QObject):
    """Runs a blocking call on a worker thread and calls back on the UI thread."""

    _done = Signal(object, object, object)   # callback, result, error

    def __init__(self):
        super().__init__()
        self._done.connect(lambda callback, result, error: callback(result, error))

    def run(self, fn, callback):
        def work():
            try:
                self._done.emit(callback, fn(), None)
            except Exception as e:
                self._done.emit(callback, None, e)
        threading.Thread(target=work, daemon=True).start()


def config_changed(old, new, *keys):
    """True when the value at `keys` differs between two config snapshots."""
    for k in keys:
        old = old.get(k, {}) if isinstance(old, dict) else None
        new = new.get(k, {}) if isinstance(new, dict) else None
    return old != new


class WhisperWriterApp(QObject):
    """Wires the engine, recorder, session store, window, overlay and tray together."""

    def __init__(self):
        super().__init__()
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        self.app = QApplication(sys.argv)
        self.app.setApplicationName('WhisperWriter')
        self.app.setQuitOnLastWindowClosed(False)   # tray app: closing the window keeps it running
        self.instance_server = None
        self.main_window = None
        if not self._claim_single_instance():
            sys.exit(0)

        ConfigManager.initialize()
        first_run = not ConfigManager.config_file_exists()
        if first_run and IS_FROZEN and os.path.isfile(BUNDLED_SERVER):
            # The release build ships whisper.cpp (Vulkan, with CPU fallback): use it.
            ConfigManager.set_config_value('whispercpp', 'model_options', 'engine')
        self._apply_theme()

        self.store = SessionStore(ConfigManager.get_config_value('sessions', 'db_path') or None)
        self.active_session_id = self.store.resolve_active_session(self._gap_minutes())
        self.store.prune_empty_sessions(keep_id=self.active_session_id)

        self.tasks = BackgroundTasks()
        self.engine = EngineManager()
        self.engine.statusChanged.connect(self.on_engine_status)
        self.input_simulator = InputSimulator()
        self.overlay = self._build_overlay()
        self.dictation = DictationController(self.engine, self.overlay, self.input_simulator)
        self.dictation.dictated.connect(self.on_dictated)
        self._build_window()

        self.bridge = KeyBridge()
        self.bridge.activated.connect(self.on_hotkey_pressed, Qt.ConnectionType.QueuedConnection)
        self.bridge.deactivated.connect(self.dictation.on_hotkey_released, Qt.ConnectionType.QueuedConnection)
        self.key_listener = KeyListener()
        self.key_listener.add_callback('on_activate', self.bridge.activated.emit)
        self.key_listener.add_callback('on_deactivate', self.bridge.deactivated.emit)
        self.key_listener.start()

        self.create_tray_icon()
        self.engine.start()

        if first_run:
            self.main_window.show()
            self.main_window.show_settings('engine')
            self.main_window.show_toast('Welcome! Pick your engine and hit Save & apply.', 6000)
        elif '--autostart' in sys.argv and ConfigManager.get_config_value('misc', 'start_minimized'):
            print('WhisperWriter is running in the system tray.')
        else:
            self.main_window.show()

    # ================================================================ building
    @staticmethod
    def _appearance():
        a = ConfigManager.get_config_section('appearance')
        return a.get('theme') or 'dark', a.get('accent') or 'orange'

    def _apply_theme(self):
        theme.apply_app_theme(self.app, *self._appearance())
        self.app.setWindowIcon(theme.app_icon())

    def _build_overlay(self):
        overlay = RecorderOverlay()
        overlay.stopRequested.connect(lambda: self.dictation.stop())
        overlay.cancelRequested.connect(lambda: self.dictation.cancel())
        return overlay

    def _build_window(self):
        """Create the main window; called again after a theme change to restyle everything."""
        old = self.main_window
        w = MainWindow(self.store)
        w.recordClicked.connect(self.dictation.toggle_from_button)
        w.newSessionRequested.connect(self.new_session)
        w.continueSessionRequested.connect(self.set_active_session)
        w.sendToClaudeRequested.connect(self.send_session_to_claude)
        w.sendTextToClaudeRequested.connect(lambda text: self.send_to_claude(text, 'dictation'))
        w.summarizeRequested.connect(self.summarize_session)
        w.settingChangeRequested.connect(self.change_setting)
        w.settingsSaved.connect(self.apply_settings)
        w.mcpSetupRequested.connect(self.setup_mcp)
        w.restartEngineRequested.connect(self.engine.start)
        self.dictation.statusChanged.connect(w.set_recording_state)
        self.dictation.levelChanged.connect(w.push_level)
        self.dictation.levelChanged.connect(self.overlay.push_level)
        w.set_active_session(self.active_session_id)
        w.set_engine_status(self.engine.status)
        w.refresh_mcp(claude_integration.is_registered())
        self.main_window = w
        if old:
            w.setGeometry(old.geometry())
            on_settings = old.pages.currentIndex() == 1
            for signal, slot in ((self.dictation.statusChanged, old.set_recording_state),
                                 (self.dictation.levelChanged, old.push_level)):
                signal.disconnect(slot)
            if old.isVisible():
                w.show()
                if on_settings:
                    w.show_settings('appearance')
            old.deleteLater()

    def _rebuild_ui(self):
        self._apply_theme()
        self.dictation.levelChanged.disconnect(self.overlay.push_level)
        self.overlay.deleteLater()
        self.overlay = self._build_overlay()
        self.dictation.overlay = self.overlay
        self._build_window()
        self.tray_icon.setIcon(theme.app_icon())

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
        if self.main_window:
            self.main_window.bring_to_front()

    def cleanup(self):
        self.dictation.shutdown()
        self.key_listener.stop()
        self.input_simulator.cleanup()
        self.engine.stop()
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
        menu.addAction('Open WhisperWriter', lambda: self.main_window.bring_to_front())
        menu.addAction('New session', self.new_session)
        menu.addAction('Search…', lambda: self.main_window.open_palette())
        menu.addAction('Settings', lambda: self.main_window.show_settings())
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

    # ================================================================ engine & settings
    def on_engine_status(self, status):
        self.main_window.set_engine_status(status)
        self.tray_status.setText(status.title)
        self.tray_icon.setToolTip(f'WhisperWriter — {status.title}')
        if status.state is EngineState.ERROR:
            self.overlay.show_message(status.detail or status.title, head=status.title)

    def change_setting(self, keys, value):
        """A single setting changed from the main window (quick toggle, recording mode)."""
        old = ConfigManager.snapshot()
        ConfigManager.set_config_value(value, *keys)
        ConfigManager.save_config()
        self.apply_settings(old)

    def apply_settings(self, old):
        """Apply a saved config in-process, restarting only what changed."""
        new = ConfigManager.snapshot()
        changed = lambda *keys: config_changed(old, new, *keys)   # noqa: E731

        if (changed('model_options', 'engine') or changed('model_options', 'local')
                or changed('model_options', 'whispercpp')
                or any(changed('post_processing', 'llm_correction', k) for k in LLM_SERVER_KEYS)):
            self.engine.start()

        if (changed('recording_options', 'activation_key')
                or changed('recording_options', 'activation_key_no_enter')):
            self.key_listener.update_activation_keys()
        if changed('recording_options', 'input_backend'):
            self.key_listener.stop()
            self.key_listener.update_backend()
            self.key_listener.start()

        if changed('post_processing', 'input_method'):
            self.input_simulator.cleanup()
            self.input_simulator = InputSimulator()
            self.dictation.input_simulator = self.input_simulator

        if changed('sessions', 'db_path'):
            self.store.close()
            self.store = SessionStore(new['sessions'].get('db_path') or None)
            self.main_window.set_store(self.store)
            self.active_session_id = self.store.resolve_active_session(self._gap_minutes())
            self.main_window.set_active_session(self.active_session_id)

        if changed('appearance'):
            self._rebuild_ui()
        else:
            self.main_window.refresh_config_views()
        self.main_window.show_toast('Settings saved and applied')

    def setup_mcp(self):
        db = ConfigManager.get_config_value('sessions', 'db_path')
        self.tasks.run(lambda: claude_integration.register(db), self._on_mcp_registered)

    def _on_mcp_registered(self, result, error):
        ok, message = result if result else (False, str(error))
        self.main_window.refresh_mcp(claude_integration.is_registered(), message)
        self.main_window.show_toast(message if ok else 'Could not register — see Settings → Claude Code')

    # ================================================================ sessions
    @staticmethod
    def _gap_minutes():
        return ConfigManager.get_config_value('sessions', 'new_session_after_minutes') or 0

    def set_active_session(self, session_id):
        self.active_session_id = session_id
        self.store.touch_session(session_id)
        self.main_window.set_active_session(session_id)

    def new_session(self):
        current = self.store.get_session(self.active_session_id) if self.active_session_id else None
        if not current or current['dictation_count'] > 0:
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
        return self.active_session_id

    def on_hotkey_pressed(self):
        self.dictation.on_hotkey_pressed(suppress_enter=self.key_listener.suppress_enter)

    def on_dictated(self, result, target, typed):
        if typed:
            self.main_window.set_last_target(target.label)
        if not ConfigManager.get_config_value('sessions', 'enabled'):
            return
        session_id = self._session_for_new_dictation()
        dictation_id = self.store.add_dictation(
            session_id, result.text, raw_text=result.raw, duration_s=result.duration_s,
            latency_s=result.latency_s, target_app=target.label if typed else None,
            target_title=target.title if typed else None,
            engine=self.engine.kind.value if self.engine.kind else None, corrected=result.corrected)
        dictation = next((d for d in self.store.dictations(session_id, limit=1)
                          if d['id'] == dictation_id), None)
        if dictation:
            self.main_window.add_dictation(session_id, dictation)

    def summarize_session(self, session_id):
        transcript = self.store.session_markdown(session_id)
        self.tasks.run(lambda: summarize_with_llm(transcript),
                       lambda summary, error: self._on_summary(session_id, summary, error))

    def _on_summary(self, session_id, summary, error):
        if error:
            session = self.store.get_session(session_id)
            self.main_window.set_summary(session_id, session and session.get('summary'))
            self.main_window.show_toast(f'Summary failed: {error}', 6000)
            return
        self.store.set_summary(session_id, summary)
        self.main_window.set_summary(session_id, summary)
        self.main_window.refresh_sessions()
        self.main_window.show_toast('Summary saved to the session')

    def send_session_to_claude(self, session_id):
        """Whole session (with its summary) as a <dictation-session> context block."""
        self.send_to_claude(self.store.session_markdown(session_id, for_claude=True), 'session')

    def send_to_claude(self, text, what):
        """
        Paste `text` into the most recently used Claude window (desktop app or Claude Code
        terminal) and, with submit_on_send, press Enter. Falls back to the clipboard.
        """
        target = find_claude_code_window()
        if target is None or not focus_window(target.hwnd):
            QGuiApplication.clipboard().setText(text)
            self.main_window.show_toast(f'No Claude window found — the {what} is on your '
                                        'clipboard, paste it with Ctrl+V', 5000)
            return
        self.input_simulator.paste(text)
        if ConfigManager.get_config_value('claude_code', 'submit_on_send'):
            time.sleep(0.2)   # let the paste land before submitting
            self.input_simulator.press_enter()
            self.main_window.show_toast(f'Sent the {what} to {target.label}', 3000)
        else:
            self.main_window.show_toast(f'Pasted the {what} into {target.label} — '
                                        'add your question and press Enter', 4000)


def main():
    os.chdir(APP_ROOT)   # relative asset paths (assets/beep.wav) resolve from the app folder
    install_crash_log()
    set_windows_app_id()
    WhisperWriterApp().run()


if __name__ == '__main__':
    main()
