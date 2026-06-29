import os
import sys
import time
from audioplayer import AudioPlayer
from pynput.keyboard import Controller
from PyQt5.QtCore import QObject, QProcess
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

        # Kies de transcriptie-engine. Bij 'whispercpp' start een lokale GPU-server
        # (Vulkan) en is er geen in-proces faster-whisper-model nodig.
        self.engine = resolve_engine()
        self.whispercpp_server = None
        if self.engine == 'whispercpp':
            self.local_model = None
            wc_options = ConfigManager.get_config_section('model_options').get('whispercpp', {})
            if wc_options.get('auto_start', True):
                self.whispercpp_server = WhisperCppServer()
                self.whispercpp_server.start()
        elif self.engine == 'faster-whisper':
            self.local_model = create_local_model()
        else:  # openai-api
            self.local_model = None

        self.result_thread = None

        self.main_window = MainWindow()
        self.main_window.openSettings.connect(self.settings_window.show)
        self.main_window.startListening.connect(self.key_listener.start)
        self.main_window.closeApp.connect(self.exit_app)

        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.status_window = StatusWindow()

        self.create_tray_icon()
        self.main_window.show()

    def _engine_label(self):
        """Leesbare naam van de actieve transcriptie-engine."""
        return {
            'whispercpp': 'whisper.cpp GPU (Vulkan)',
            'faster-whisper': 'faster-whisper (CPU/CUDA)',
            'openai-api': 'OpenAI API',
        }.get(self.engine, self.engine)

    def _status_text(self):
        """Statusregel voor tray-tooltip en -menu (ververst bij openen)."""
        parts = [f'WhisperWriter actief — {self._engine_label()}']
        if self.engine == 'whispercpp':
            available = self.whispercpp_server.is_available() if self.whispercpp_server \
                else WhisperCppServer().is_available()
            parts.append('GPU-server: ' + ('draait ✓' if available else 'niet bereikbaar ✗'))
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

        # Status-regel bovenaan (niet klikbaar) zodat je ziet dat het draait.
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
        exit_action = QAction('Afsluiten (stopt ook de GPU-server)', self.app)
        exit_action.triggered.connect(self.exit_app)
        tray_menu.addAction(exit_action)

        # Status verversen telkens als het menu opent.
        tray_menu.aboutToShow.connect(self._update_tray_status)
        self._update_tray_status()

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

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
