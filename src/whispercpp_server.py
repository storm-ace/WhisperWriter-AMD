"""
Manage a local whisper.cpp `whisper-server` subprocess (GPU via Vulkan).

By default WhisperWriter runs faster-whisper in-process. On machines without an NVIDIA
GPU (e.g. AMD iGPUs), faster-whisper/ctranslate2 cannot use the GPU. This module instead
launches a local whisper.cpp server with the Vulkan backend and keeps the model resident,
so each dictation only costs inference time (not model loading).

The server runs as a separate process so a crash in the GPU engine does not affect the
app's hotkey/clipboard logic. Transcription itself goes over HTTP
(`transcribe_whispercpp` in transcription.py).
"""

import os
import socket
import subprocess
import time

from utils import ConfigManager


class WhisperCppServer:
    """Start, monitor and stop a whisper.cpp `whisper-server` process."""

    def __init__(self):
        self.process = None
        options = ConfigManager.get_config_section('model_options').get('whispercpp', {})
        self.binary_path = options.get('binary_path')
        self.model_path = options.get('model_path')
        self.host = options.get('host') or '127.0.0.1'
        self.port = int(options.get('port') or 8080)
        self.n_threads = options.get('n_threads')
        # Directory with the MinGW runtime DLLs (libstdc++, libgomp, ...). Added to PATH
        # so a self-built whisper-server can find its dependencies.
        self.lib_path = options.get('lib_path')

    def _server_url(self):
        return f'http://{self.host}:{self.port}'

    def _is_port_open(self):
        """True once the server is listening on the port (i.e. the model is loaded)."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((self.host, self.port)) == 0

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def is_available(self):
        """True if the server is reachable (started by this app or already running)."""
        return self._is_port_open()

    def start(self, wait_timeout=120):
        """
        Start the whisper-server and wait until it is listening (= model loaded).

        :return: True if the server is running, otherwise False.
        """
        if self._is_port_open():
            # Something is already listening on this port (e.g. started manually). Reuse it.
            ConfigManager.console_print(
                f'whisper.cpp server already reachable at {self._server_url()}; reusing it.')
            return True

        if not self.binary_path or not os.path.isfile(self.binary_path):
            ConfigManager.console_print(
                f'whisper.cpp server not started: invalid binary_path ({self.binary_path}).')
            return False
        if not self.model_path or not os.path.isfile(self.model_path):
            ConfigManager.console_print(
                f'whisper.cpp server not started: invalid model_path ({self.model_path}).')
            return False

        cmd = [
            self.binary_path,
            '-m', self.model_path,
            '--host', self.host,
            '--port', str(self.port),
        ]
        if self.n_threads:
            cmd += ['-t', str(self.n_threads)]

        # Extend PATH so the exe finds its DLLs: its own build dir (ggml*.dll) and the
        # MinGW runtime dir. vulkan-1.dll lives in System32 and is always found.
        env = os.environ.copy()
        extra_paths = [os.path.dirname(self.binary_path)]
        if self.lib_path:
            extra_paths.append(self.lib_path)
        env['PATH'] = os.pathsep.join(extra_paths + [env.get('PATH', '')])

        ConfigManager.console_print(f'Starting whisper.cpp server: {" ".join(cmd)}')
        creationflags = 0
        if os.name == 'nt':
            # Avoid a console window flashing up during the hidden (pythonw) autostart.
            creationflags = subprocess.CREATE_NO_WINDOW
        self.process = subprocess.Popen(
            cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creationflags)

        # Wait until the port is open (loading the model takes a few seconds).
        deadline = time.time() + wait_timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                ConfigManager.console_print(
                    f'whisper.cpp server exited immediately (exit {self.process.returncode}).')
                self.process = None
                return False
            if self._is_port_open():
                ConfigManager.console_print(
                    f'whisper.cpp server ready at {self._server_url()}.')
                return True
            time.sleep(0.5)

        ConfigManager.console_print('whisper.cpp server did not start in time; aborting.')
        self.stop()
        return False

    def stop(self):
        """Stop the server process (if it was started by this app)."""
        if self.process is not None and self.process.poll() is None:
            ConfigManager.console_print('Stopping whisper.cpp server...')
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
