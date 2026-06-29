"""
Beheer van een lokale whisper.cpp `whisper-server`-subprocess (GPU via Vulkan).

WhisperWriter draait standaard faster-whisper in-proces. Op machines zonder NVIDIA
(bv. AMD-iGPU's) kan faster-whisper/ctranslate2 niet naar de GPU. Deze module start in
plaats daarvan een lokale whisper.cpp-server met de Vulkan-backend en houdt het model
resident, zodat elke dicteer-actie alleen de inferentie kost (niet het modelladen).

De server wordt als los proces gestart zodat een crash in de GPU-engine de hotkey-/
klembord-logica van de app niet raakt. De transcriptie zelf loopt via HTTP
(`transcribe_http` in transcription.py).
"""

import os
import socket
import subprocess
import time

from utils import ConfigManager


class WhisperCppServer:
    """Start, bewaak en stop een whisper.cpp `whisper-server`-proces."""

    def __init__(self):
        self.process = None
        options = ConfigManager.get_config_section('model_options').get('whispercpp', {})
        self.binary_path = options.get('binary_path')
        self.model_path = options.get('model_path')
        self.host = options.get('host') or '127.0.0.1'
        self.port = int(options.get('port') or 8080)
        self.n_threads = options.get('n_threads')
        # Map met de MinGW-runtime-DLL's (libstdc++, libgomp, ...). Wordt aan PATH
        # toegevoegd zodat de zelfgebouwde whisper-server zijn afhankelijkheden vindt.
        self.lib_path = options.get('lib_path')

    def _server_url(self):
        return f'http://{self.host}:{self.port}'

    def _is_port_open(self):
        """True zodra de server op de poort luistert (model is dan al geladen)."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((self.host, self.port)) == 0

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def is_available(self):
        """True als de server bereikbaar is (door deze app gestart of extern draaiend)."""
        return self._is_port_open()

    def start(self, wait_timeout=120):
        """
        Start de whisper-server en wacht tot hij luistert (= model geladen).

        :return: True als de server draait, anders False.
        """
        if self._is_port_open():
            # Er draait al iets op deze poort (bv. handmatig gestart). Hergebruik het.
            ConfigManager.console_print(
                f'whisper.cpp-server al bereikbaar op {self._server_url()}; hergebruik.')
            return True

        if not self.binary_path or not os.path.isfile(self.binary_path):
            ConfigManager.console_print(
                f'whisper.cpp-server niet gestart: binary_path ongeldig ({self.binary_path}).')
            return False
        if not self.model_path or not os.path.isfile(self.model_path):
            ConfigManager.console_print(
                f'whisper.cpp-server niet gestart: model_path ongeldig ({self.model_path}).')
            return False

        cmd = [
            self.binary_path,
            '-m', self.model_path,
            '--host', self.host,
            '--port', str(self.port),
        ]
        if self.n_threads:
            cmd += ['-t', str(self.n_threads)]

        # PATH uitbreiden zodat de exe zijn DLL's vindt: de eigen build-map (ggml*.dll)
        # en de MinGW-runtime-map. vulkan-1.dll zit in System32 en wordt altijd gevonden.
        env = os.environ.copy()
        extra_paths = [os.path.dirname(self.binary_path)]
        if self.lib_path:
            extra_paths.append(self.lib_path)
        env['PATH'] = os.pathsep.join(extra_paths + [env.get('PATH', '')])

        ConfigManager.console_print(f'whisper.cpp-server starten: {" ".join(cmd)}')
        creationflags = 0
        if os.name == 'nt':
            # Geen consolevenster laten opflitsen bij de verborgen (pythonw) autostart.
            creationflags = subprocess.CREATE_NO_WINDOW
        self.process = subprocess.Popen(
            cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creationflags)

        # Wachten tot de poort open is (model laden duurt enkele seconden).
        deadline = time.time() + wait_timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                ConfigManager.console_print(
                    f'whisper.cpp-server stopte direct (exit {self.process.returncode}).')
                self.process = None
                return False
            if self._is_port_open():
                ConfigManager.console_print(
                    f'whisper.cpp-server klaar op {self._server_url()}.')
                return True
            time.sleep(0.5)

        ConfigManager.console_print('whisper.cpp-server niet op tijd gestart; afgebroken.')
        self.stop()
        return False

    def stop(self):
        """Stop het server-proces (indien gestart door deze app)."""
        if self.process is not None and self.process.poll() is None:
            ConfigManager.console_print('whisper.cpp-server stoppen...')
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
