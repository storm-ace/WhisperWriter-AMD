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
import re
import socket
import subprocess
import time

import glob

import requests

from model_setup import models_dirs
from sessions import app_data_dir
from utils import APP_ROOT, ConfigManager

_VULKAN_DEVICE = re.compile(r'ggml_vulkan: 0 = (.+?)(?: \(|\s*\|)')
_BACKEND = re.compile(r'using (\w+?)\d* backend')


# The release build ships whisper-server here; ggml models dropped into models/ (or
# downloaded there on first start) are found.
BUNDLED_SERVER = os.path.join(APP_ROOT, 'whisper.cpp', 'whisper-server.exe')
_MODEL_PREFERENCE = ('large-v3-turbo', 'large-v3', 'medium', 'small', 'base')


def bundled_model():
    """Best ggml model found in the models folders, or None."""
    found = [p for d in models_dirs() for p in glob.glob(os.path.join(d, 'ggml-*.bin'))]
    for name in _MODEL_PREFERENCE:
        for path in found:
            if os.path.basename(path) == f'ggml-{name}.bin':
                return path
    return found[0] if found else None


# Address of the whisper-server the engine actually uses. It differs from the configured
# port when that port was taken by another program and a free one was picked instead.
_active_url = None


def active_url():
    """Base URL of the running whisper-server, or None when the engine did not set one."""
    return _active_url


def _free_port(host):
    """A port on `host` that nothing is bound to right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


class WhisperCppServer:
    """Start, monitor and stop a whisper.cpp `whisper-server` process."""

    def __init__(self):
        self.process = None
        self._log_file = None
        # The server's stderr is kept so the GPU it picked can be shown in the app.
        self.log_path = os.path.join(app_data_dir(), 'whisper-server.log')
        options = ConfigManager.get_config_section('model_options').get('whispercpp', {})
        self.binary_path = options.get('binary_path') or (
            BUNDLED_SERVER if os.path.isfile(BUNDLED_SERVER) else None)
        self.model_path = options.get('model_path') or bundled_model()
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

    def _is_whisper_server(self):
        """
        True if what listens on the port is a whisper-server. Another program on the same
        port (e.g. a WSL/Docker service forwarded to 8080) answers /inference with errors.
        """
        try:
            resp = requests.get(self._server_url() + '/health', timeout=2)
        except requests.RequestException:
            return False
        return 'whisper.cpp' in resp.headers.get('Server', '').lower()

    def _set_active(self):
        global _active_url
        _active_url = self._server_url()

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def is_available(self):
        """True if a whisper-server is reachable (started by this app or already running)."""
        if self._is_port_open() and self._is_whisper_server():
            self._set_active()
            return True
        return False

    def start(self, wait_timeout=120):
        """
        Start the whisper-server and wait until it is listening (= model loaded).

        :return: True if the server is running, otherwise False.
        """
        if self._is_port_open():
            if self._is_whisper_server():
                # A whisper-server is already running here (e.g. started manually). Reuse it.
                ConfigManager.console_print(
                    f'whisper.cpp server already reachable at {self._server_url()}; reusing it.')
                self._set_active()
                return True
            taken = self.port
            self.port = _free_port(self.host)
            ConfigManager.console_print(
                f'Port {taken} is used by another program; starting whisper.cpp on port {self.port}.')

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
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self._log_file = open(self.log_path, 'w', encoding='utf-8', errors='replace')
        self.process = subprocess.Popen(
            cmd, env=env, stdout=subprocess.DEVNULL, stderr=self._log_file,
            creationflags=creationflags)
        process = self.process

        # Wait until the port is open (loading the model takes a few seconds).
        deadline = time.time() + wait_timeout
        while time.time() < deadline:
            if self.process is not process:
                return False  # stop() was called while the model was loading
            if process.poll() is not None:
                ConfigManager.console_print(
                    f'whisper.cpp server exited immediately (exit {process.returncode}).')
                self.process = None
                return False
            if self._is_port_open():
                ConfigManager.console_print(
                    f'whisper.cpp server ready at {self._server_url()}.')
                self._set_active()
                return True
            time.sleep(0.5)

        ConfigManager.console_print('whisper.cpp server did not start in time; aborting.')
        self.stop()
        return False

    def device_info(self):
        """
        (backend, device) the server reported at start-up, e.g. ('Vulkan', 'AMD Radeon(TM)
        8060S Graphics') or ('CPU', None). (None, None) when unknown, e.g. for a server that
        was already running when the app started.
        """
        try:
            with open(self.log_path, encoding='utf-8', errors='replace') as f:
                log = f.read()
        except OSError:
            return None, None
        backend = _BACKEND.search(log)
        device = _VULKAN_DEVICE.search(log)
        return (backend.group(1) if backend else None), (device.group(1).strip() if device else None)

    def stop(self):
        """Stop the server process (if it was started by this app)."""
        global _active_url
        if _active_url == self._server_url():
            _active_url = None
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            ConfigManager.console_print('Stopping whisper.cpp server...')
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._log_file:
            self._log_file.close()
            self._log_file = None
