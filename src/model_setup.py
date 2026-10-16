"""
Installs what the transcription engines need on first use, so users do not have to:

- faster-whisper: pip-installed into the running interpreter when started from source
  (the portable release does not ship it; there the engine falls back to whisper.cpp).
- whisper.cpp: the ggml model is downloaded into models/ when none is present.
"""

import importlib
import importlib.util
import os
import subprocess
import sys

import requests

from utils import APP_ROOT, IS_FROZEN, ConfigManager

GGML_DEFAULT_MODEL = 'large-v3-turbo'
GGML_URL = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin'
# Pinned like requirements.txt; ctranslate2 imports pkg_resources, gone in setuptools>=81.
FASTER_WHISPER_PACKAGES = ['faster-whisper==1.0.2', 'ctranslate2==4.2.1', 'setuptools<81']


def faster_whisper_available():
    return importlib.util.find_spec('faster_whisper') is not None


def can_install_packages():
    """pip only works from source; the release build has no interpreter to install into."""
    return not IS_FROZEN


def install_faster_whisper():
    """pip-install faster-whisper into this interpreter. Raises RuntimeError on failure."""
    ConfigManager.console_print('faster-whisper not found; installing it...')
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check']
        + FASTER_WHISPER_PACKAGES,
        capture_output=True, text=True, creationflags=creationflags)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or '').strip().splitlines()[-3:]
        raise RuntimeError('Installing faster-whisper failed: ' + ' '.join(tail))
    importlib.invalidate_caches()
    if not faster_whisper_available():
        raise RuntimeError('faster-whisper was installed but cannot be imported; restart the app.')
    ConfigManager.console_print('faster-whisper installed.')


def models_dirs():
    """Where ggml models are looked for: models/ next to the app, then the per-user folder."""
    from sessions import app_data_dir
    return [os.path.join(APP_ROOT, 'models'), os.path.join(app_data_dir(), 'models')]


def _writable_models_dir():
    for path in models_dirs():
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, '.write-test')
            with open(probe, 'w'):
                pass
            os.remove(probe)
            return path
        except OSError:
            continue
    raise RuntimeError('No writable folder for the model (tried: ' + ', '.join(models_dirs()) + ').')


def download_ggml_model(name=GGML_DEFAULT_MODEL, progress=None, cancelled=None):
    """
    Download ggml-<name>.bin into a models folder and return its path.

    progress(done_bytes, total_bytes) is called while downloading; cancelled() returning
    True aborts. The file is written as .part and renamed when complete, so an interrupted
    download is never mistaken for a model.
    """
    target = os.path.join(_writable_models_dir(), f'ggml-{name}.bin')
    part = target + '.part'
    url = GGML_URL.format(name=name)
    ConfigManager.console_print(f'Downloading {url} -> {target}')
    try:
        with requests.get(url, stream=True, timeout=30) as response:
            response.raise_for_status()
            total = int(response.headers.get('Content-Length') or 0)
            done = 0
            with open(part, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    if cancelled and cancelled():
                        raise RuntimeError('Model download cancelled.')
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        if total and os.path.getsize(part) != total:
            raise RuntimeError('Model download incomplete; it will be retried on the next start.')
        os.replace(part, target)
    except requests.RequestException as e:
        raise RuntimeError(f'Downloading the {name} model failed: {e}') from e
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
    ConfigManager.console_print('Model downloaded.')
    return target
