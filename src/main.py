import os
import sys
import time
from audioplayer import AudioPlayer
from pynput.keyboard import Controller
from PyQt5.QtCore import QObject, QProcess, QSharedMemory
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox

from key_listener import KeyListener
from result_thread import ResultThread
from ui.main_window import MainWindow
from ui.settings_window import SettingsWindow
from ui.status_window import StatusWindow
from transcription import create_local_model, resolve_engine
from input_simulation import InputSimulator
from utils import ConfigManager
from whispercpp_server import WhisperCppServer


class WhisperWriterApp(QObject):
    def __init__(self):
        """
        Initialize the application, opening settings window if no configuration file is found.
        """
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setWindowIcon(QIcon(os.path.join('assets', 'ww-logo.png')))

        # Enforce a single running instance (avoids double hotkeys and server conflicts).
        # On Windows the segment is released automatically when the process exits.
        self._single_instance = QSharedMemory('WhisperWriter-AMD-single-instance')
        if not self._single_instance.create(1):
            QMessageBox.information(
                None, 'WhisperWriter',
                'WhisperWriter is already running — check the system tray.')
            sys.exit(0)

        ConfigManager.initialize()

        self.settings_window = SettingsWindow()
        self.settings_window.settings_closed.connect(self.on_settings_closed)
        self.settings_window.settings_saved.connect(self.restart_app)

        if ConfigManager.config_file_exists():
            self.initialize_components()
        else:
            print('No valid configuration file found. Opening settings window...')
            self.settings_window.show()

    def initialize_components(self):
        """
        Initialize the components of the application.
        """
        self.input_simulator = InputSimulator()

        self.key_listener = KeyListener()
        self.key_listener.add_callback("on_activate", self.on_activation)
        self.key_listener.add_callback("on_deactivate", self.on_deactivation)

        # Pick the transcription engine. For 'whispercpp' a local GPU server (Vulkan)
        # is launched and no in-process faster-whisper model is needed.
        self.engine = resolve_engine()
        self.whispercpp_server = None
        if self.engine == 'whispercpp':
            self.local_model = None
            wc_options = ConfigManager.get_config_section('model_options').get('whispercpp', {})
            if wc_options.get('auto_start', True):
                self.whispercpp_server = WhisperCppServer()
                self.whispercpp_server.start()
        else:  # faster-whisper
            self.local_model = create_local_model()

        self.result_thread = None

        self.main_window = MainWindow()
        self.main_window.openSettings.connect(self.settings_window.show)
        self.main_window.startListening.connect(self.key_listener.start)

        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.status_window = StatusWindow()

        self.create_tray_icon()
        self.main_window.show()

    def _engine_label(self):
        """Human-readable name of the active transcription engine."""
        return {
            'whispercpp': 'whisper.cpp GPU (Vulkan)',
            'faster-whisper': 'faster-whisper (CPU)',
        }.get(self.engine, self.engine)

    def _status_text(self):
        """Status line for the tray tooltip and menu (refreshed when opened)."""
        parts = [f'WhisperWriter running - {self._engine_label()}']
        if self.engine == 'whispercpp':
            available = self.whispercpp_server.is_available() if self.whispercpp_server \
                else WhisperCppServer().is_available()
            parts.append('GPU server: ' + ('running' if available else 'unreachable'))
        return '  |  '.join(parts)

    def _update_tray_status(self):
        text = self._status_text()
        self.status_action.setText(text)
        self.tray_icon.setToolTip(text)

    def create_tray_icon(self):
        """
        Create the system tray icon and its context menu.
        """
        self.tray_icon = QSystemTrayIcon(QIcon(os.path.join('assets', 'ww-logo.png')), self.app)

        tray_menu = QMenu()

        # Non-clickable status line at the top so you can see it is running.
        self.status_action = QAction('', self.app)
        self.status_action.setEnabled(False)
        tray_menu.addAction(self.status_action)
        tray_menu.addSeparator()

        show_action = QAction('WhisperWriter Main Menu', self.app)
        show_action.triggered.connect(self.main_window.show)
        tray_menu.addAction(show_action)

        settings_action = QAction('Open Settings', self.app)
        settings_action.triggered.connect(self.settings_window.show)
        tray_menu.addAction(settings_action)

        tray_menu.addSeparator()
        exit_action = QAction('Exit (also stops the GPU server)', self.app)
        exit_action.triggered.connect(self.exit_app)
        tray_menu.addAction(exit_action)

        # Refresh the status each time the menu opens.
        tray_menu.aboutToShow.connect(self._update_tray_status)
        self._update_tray_status()

        self.tray_icon.setContextMenu(tray_menu)
        # Double-clicking the tray icon opens the settings window.
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        """Open settings on a double-click of the tray icon."""
        if reason == QSystemTrayIcon.DoubleClick:
            self.settings_window.show()
            self.settings_window.raise_()
            self.settings_window.activateWindow()

    def cleanup(self):
        if self.key_listener:
            self.key_listener.stop()
        if self.input_simulator:
            self.input_simulator.cleanup()
        if getattr(self, 'whispercpp_server', None):
            self.whispercpp_server.stop()

    def exit_app(self):
        """
        Exit the application.
        """
        self.cleanup()
        QApplication.quit()

    def restart_app(self):
        """Restart the application to apply the new settings."""
        self.cleanup()
        # Release the single-instance lock so the relaunched process can acquire it.
        if getattr(self, '_single_instance', None) is not None:
            self._single_instance.detach()
        QApplication.quit()
        QProcess.startDetached(sys.executable, sys.argv)

    def on_settings_closed(self):
        """
        If settings is closed without saving on first run, initialize the components with default values.
        """
        if not os.path.exists(os.path.join('src', 'config.yaml')):
            QMessageBox.information(
                self.settings_window,
                'Using Default Values',
                'Settings closed without saving. Default values are being used.'
            )
            self.initialize_components()

    def on_activation(self):
        """
        Called when the activation key combination is pressed.
        """
        # Remember whether the triggering key was the secondary ("no Enter") key,
        # captured at activation time so the result handler can honor it later.
        self.suppress_enter_current = self.key_listener.suppress_enter

        if self.result_thread and self.result_thread.isRunning():
            recording_mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
            if recording_mode == 'press_to_toggle':
                self.result_thread.stop_recording()
            elif recording_mode == 'continuous':
                self.stop_result_thread()
            return

        self.start_result_thread()

    def on_deactivation(self):
        """
        Called when the activation key combination is released.
        """
        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'hold_to_record':
            if self.result_thread and self.result_thread.isRunning():
                self.result_thread.stop_recording()

    def start_result_thread(self):
        """
        Start the result thread to record audio and transcribe it.
        """
        if self.result_thread and self.result_thread.isRunning():
            return

        self.result_thread = ResultThread(self.local_model)
        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.result_thread.statusSignal.connect(self.status_window.updateStatus)
            self.status_window.closeSignal.connect(self.stop_result_thread)
        self.result_thread.resultSignal.connect(self.on_transcription_complete)
        self.result_thread.start()

    def stop_result_thread(self):
        """
        Stop the result thread.
        """
        if self.result_thread and self.result_thread.isRunning():
            self.result_thread.stop()

    def on_transcription_complete(self, result):
        """
        When the transcription is complete, type the result and start listening for the activation key again.
        """
        self.input_simulator.typewrite(result)

        press_enter = (ConfigManager.get_config_value('post_processing', 'press_enter')
                       and not getattr(self, 'suppress_enter_current', False))
        if press_enter:
            self.input_simulator.press_enter()

        if ConfigManager.get_config_value('misc', 'noise_on_completion'):
            AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=True)

        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous':
            self.start_result_thread()
        else:
            self.key_listener.start()

    def run(self):
        """
        Start the application.
        """
        sys.exit(self.app.exec_())


if __name__ == '__main__':
    app = WhisperWriterApp()
    app.run()
