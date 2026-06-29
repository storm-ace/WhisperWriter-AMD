"""
Manage a local llama.cpp `llama-server` subprocess (GPU via Vulkan) for the optional
LLM transcription-correction step.

Mirrors whispercpp_server.py: launches llama-server as a separate process, keeps the
model resident, and exposes a health check. Used by transcription.correct_with_llm,
which sends the raw transcription to the server and gets a cleaned-up version back.
Runs as its own process so a crash in the LLM engine never affects dictation.
"""

import os
import socket
import subprocess
import time

from utils import ConfigManager


class LlamaCppServer:
    """Start, monitor and stop a llama.cpp `llama-server` process."""

    def __init__(self):
        self.process = None
        options = ConfigManager.get_config_section('post_processing').get('llm_correction', {})
        self.binary_path = options.get('binary_path')
        self.model_path = options.get('model_path')
        self.host = options.get('host') or '127.0.0.1'
        self.port = int(options.get('port') or 8081)
        self.n_threads = options.get('n_threads')
        self.n_gpu_layers = options.get('n_gpu_layers')
        self.context_size = options.get('context_size') or 2048
        # Directory with the MinGW runtime DLLs (libstdc++, libgomp, ...) and the
        # build's ggml*.dll — added to PATH so the self-built llama-server resolves them.
        self.lib_path = options.get('lib_path')

    def _server_url(self):
        return f'http://{self.host}:{self.port}'

    def _is_port_open(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((self.host, self.port)) == 0

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def is_available(self):
        """True if the server is reachable (started by this app or already running)."""
        return self._is_port_open()

    def start(self, wait_timeout=180):
        """
        Start llama-server and wait until it is listening (= model loaded).

        :return: True if the server is running, otherwise False.
        """
        if self._is_port_open():
            ConfigManager.console_print(
                f'llama.cpp server already reachable at {self._server_url()}; reusing it.')
            return True

        if not self.binary_path or not os.path.isfile(self.binary_path):
            ConfigManager.console_print(
                f'llama.cpp server not started: invalid binary_path ({self.binary_path}).')
            return False
        if not self.model_path or not os.path.isfile(self.model_path):
            ConfigManager.console_print(
                f'llama.cpp server not started: invalid model_path ({self.model_path}).')
            return False

        cmd = [
            self.binary_path,
            '-m', self.model_path,
            '--host', self.host,
            '--port', str(self.port),
            '-c', str(self.context_size),
            '--no-webui',
        ]
        if self.n_gpu_layers is not None:
            cmd += ['-ngl', str(self.n_gpu_layers)]
        if self.n_threads:
            cmd += ['-t', str(self.n_threads)]

        env = os.environ.copy()
        extra_paths = [os.path.dirname(self.binary_path)]
        if self.lib_path:
            extra_paths.append(self.lib_path)
        env['PATH'] = os.pathsep.join(extra_paths + [env.get('PATH', '')])

        ConfigManager.console_print(f'Starting llama.cpp server: {" ".join(cmd)}')
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.process = subprocess.Popen(
            cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creationflags)

        deadline = time.time() + wait_timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                ConfigManager.console_print(
                    f'llama.cpp server exited immediately (exit {self.process.returncode}).')
                self.process = None
                return False
            if self._is_port_open():
                ConfigManager.console_print(f'llama.cpp server ready at {self._server_url()}.')
                return True
            time.sleep(0.5)

        ConfigManager.console_print('llama.cpp server did not start in time; aborting.')
        self.stop()
        return False

    def stop(self):
        """Stop the server process (if it was started by this app)."""
        if self.process is not None and self.process.poll() is None:
            ConfigManager.console_print('Stopping llama.cpp server...')
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
