"""
Starts and stops the transcription engine (and the optional LLM correction server) off
the UI thread, and reports what it is running on.
"""

import re
import threading
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QObject, Signal

import model_setup
from llamacpp_server import LlamaCppServer
from transcription import EngineKind, create_local_model, model_label, resolve_engine
from utils import ConfigManager
from whispercpp_server import WhisperCppServer, bundled_model


class EngineState(str, Enum):
    STARTING = 'starting'
    READY = 'ready'
    ERROR = 'error'


@dataclass
class EngineStatus:
    state: EngineState
    title: str                    # one line for the status pill
    detail: str = ''              # server address, GPU, LLM state or the error
    kind: EngineKind = None
    backend: str = ''             # 'Vulkan', 'CUDA', 'CPU' or '' when unknown
    device: str = ''              # GPU name as reported by the driver
    model: str = ''

    @property
    def device_short(self):
        """'AMD Radeon(TM) 8060S Graphics' -> 'AMD Radeon 8060S'."""
        name = re.sub(r'\((TM|R)\)', '', self.device or '', flags=re.IGNORECASE)
        return re.sub(r'\s+Graphics$', '', ' '.join(name.split()))

    @property
    def compute_label(self):
        """Short label for where transcription runs, e.g. 'AMD Radeon 8060S · Vulkan'."""
        parts = [p for p in (self.device_short, self.backend) if p]
        return ' · '.join(parts) or (self.kind.label if self.kind else '')


@dataclass
class _Run:
    """One start attempt. A newer start makes older runs stale; stale runs clean up."""
    generation: int
    servers: list = field(default_factory=list)


class EngineManager(QObject):
    statusChanged = Signal(object)   # EngineStatus

    def __init__(self):
        super().__init__()
        self.local_model = None
        self.status = EngineStatus(EngineState.STARTING, 'Starting engine…')
        self._run = None
        self._generation = 0
        self._lock = threading.Lock()

    @property
    def ready(self):
        return self.status.state is EngineState.READY

    @property
    def kind(self):
        return self.status.kind

    def start(self):
        """(Re)start the engine in the background; the previous one is stopped first."""
        with self._lock:
            old = self._run
            self._generation += 1
            run = _Run(self._generation)
            self._run = run
            self.local_model = None
        self._emit(run, EngineStatus(EngineState.STARTING, 'Starting engine…'))
        threading.Thread(target=self._start_blocking, args=(old, run), daemon=True).start()

    def stop(self):
        with self._lock:
            run, self._run = self._run, None
            self.local_model = None
        if run:
            self._stop_servers(run)

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _stop_servers(run):
        for server in run.servers:
            server.stop()
        run.servers.clear()

    def _is_current(self, run):
        with self._lock:
            return self._run is run

    def _emit(self, run, status):
        if self._is_current(run):
            self.status = status
            self.statusChanged.emit(status)

    def _start_blocking(self, old, run):
        if old:
            self._stop_servers(old)
        kind = resolve_engine()
        if kind is EngineKind.FASTER_WHISPER and not model_setup.faster_whisper_available()                 and not model_setup.can_install_packages():
            # The portable release does not ship faster-whisper; it ships whisper.cpp
            # (Vulkan, with CPU fallback) instead. Switch so the app works out of the box.
            ConfigManager.console_print('faster-whisper is not part of this build; using whisper.cpp.')
            ConfigManager.set_config_value(EngineKind.WHISPERCPP.value, 'model_options', 'engine')
            ConfigManager.save_config()
            kind = EngineKind.WHISPERCPP
        try:
            status = (self._start_whispercpp(run) if kind is EngineKind.WHISPERCPP
                      else self._start_faster_whisper(run))
            if status.state is EngineState.READY:
                status.detail = self._start_llm(run, status.detail)
        except Exception as e:
            status = EngineStatus(EngineState.ERROR, 'Engine failed to start', str(e), kind)
        if self._is_current(run):
            self._emit(run, status)
        else:
            self._stop_servers(run)   # superseded while loading

    def _start_whispercpp(self, run):
        opts = ConfigManager.get_config_section('model_options').get('whispercpp', {})
        if not opts.get('model_path') and not bundled_model() and opts.get('auto_start', True):
            self._download_ggml_model(run)
        model = model_label(opts.get('model_path') or bundled_model())
        self._emit(run, EngineStatus(EngineState.STARTING, f'Loading {model or "model"} on the GPU…',
                                     kind=EngineKind.WHISPERCPP, model=model))
        server = WhisperCppServer()
        run.servers.append(server)
        ok = server.start() if opts.get('auto_start', True) else server.is_available()
        if not ok:
            return EngineStatus(EngineState.ERROR, 'whisper.cpp server not running',
                                'Check the server binary and ggml model path in Settings → Engine & GPU.',
                                EngineKind.WHISPERCPP, model=model)
        backend, device = server.device_info()
        status = EngineStatus(EngineState.READY, '', f'whisper-server at {server.host}:{server.port}',
                              EngineKind.WHISPERCPP, backend or '', device or '', model)
        if device:
            status.detail += f' · {device}'
        if backend == 'CPU':
            status.detail += ' · no GPU found: whisper.cpp runs on the CPU (build with -DGGML_VULKAN=ON)'
        status.title = ' · '.join(p for p in ('Ready', 'whisper.cpp', backend, model) if p)
        return status

    def _download_ggml_model(self, run):
        name = model_setup.GGML_DEFAULT_MODEL
        shown = [-1]

        def progress(done, total):
            pct = int(done * 100 / total) if total else -1
            if pct != shown[0]:
                shown[0] = pct
                mb = f'{done / 2**20:,.0f} / {total / 2**20:,.0f} MB' if total else f'{done / 2**20:,.0f} MB'
                self._emit(run, EngineStatus(
                    EngineState.STARTING, f'Downloading {name} model… {pct}%' if total
                    else f'Downloading {name} model…', f'First start only · {mb}',
                    EngineKind.WHISPERCPP, model=name))

        self._emit(run, EngineStatus(EngineState.STARTING, f'Downloading {name} model…',
                                     'First start only', EngineKind.WHISPERCPP, model=name))
        model_setup.download_ggml_model(name, progress, cancelled=lambda: not self._is_current(run))

    def _start_faster_whisper(self, run):
        opts = ConfigManager.get_config_section('model_options').get('local', {})
        model, device = opts.get('model') or 'base', (opts.get('device') or 'cpu').upper()
        if not model_setup.faster_whisper_available():
            self._emit(run, EngineStatus(EngineState.STARTING, 'Installing faster-whisper…',
                                         'First start only, this takes a minute',
                                         EngineKind.FASTER_WHISPER, model=model))
            model_setup.install_faster_whisper()
        self._emit(run, EngineStatus(EngineState.STARTING, f'Loading {model} on the {device}…',
                                     kind=EngineKind.FASTER_WHISPER, model=model))
        local_model = create_local_model()
        with self._lock:
            if self._run is run:
                self.local_model = local_model
        return EngineStatus(EngineState.READY, f'Ready · faster-whisper · {device} · {model}', '',
                            EngineKind.FASTER_WHISPER, device, '', model)

    def _start_llm(self, run, detail):
        llm = ConfigManager.get_config_section('post_processing').get('llm_correction', {})
        if not (llm.get('enabled') and llm.get('auto_start', True)):
            return detail
        server = LlamaCppServer()
        run.servers.append(server)
        note = 'LLM correction ready' if server.start() else 'LLM server failed (raw text is used)'
        return f'{detail} · {note}' if detail else note
