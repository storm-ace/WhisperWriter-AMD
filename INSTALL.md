# INSTALL — WhisperWriter-AMD (Windows)

Step-by-step installation on Windows, plus the problems we hit and how they were solved.
For the **AMD GPU engine** (whisper.cpp + Vulkan), see the *AMD GPU fork* section in [`README.md`](README.md).

## Requirements

- **Windows 10/11**.
- **Python 3.11** (not newer — see below). A standalone install + `venv`, or conda, both work.
- Git.
- Optional GPU:
  - **NVIDIA**: see step 4a.
  - **AMD GPU / iGPU**: faster-whisper cannot use it — use the whisper.cpp Vulkan engine instead (see README).
  - **No GPU**: see step 4b (CPU).

## Installation

### 1. Clone

```bash
git clone https://github.com/storm-ace/WhisperWriter-AMD
cd WhisperWriter-AMD
```

### 2. Python 3.11 environment

```bash
# Standalone Python 3.11 + venv:
python -m venv .venv
.venv\Scripts\activate

# ...or conda:
# conda create -n whisper-writer python=3.11 -y && conda activate whisper-writer
```

> Python 3.12+ does not work with the pinned `numba`/`llvmlite` in `requirements.txt`. 3.11 is the ceiling.

### 3. Dependencies

```bash
pip install -r requirements.txt
pip install "setuptools<81"
```

The second line is needed (see Problem 3 below) — `ctranslate2` imports `pkg_resources`,
which setuptools ≥81 no longer ships.

### 4a. NVIDIA GPU path (incl. recent cards like the RTX 50 series / Blackwell)

The pinned `ctranslate2 4.2.1` is too old for recent GPUs and lacks cuDNN 8. Upgrade to a
build with CUDA 12.x + cuDNN 9:

```bash
pip install -U ctranslate2 faster-whisper nvidia-cudnn-cu12 nvidia-cublas-cu12
```

Then in `src/config.yaml`: `engine: faster-whisper`, `device: cuda`, `compute_type: float16`.

### 4b. CPU path (no GPU)

Skip 4a. In `src/config.yaml`: `engine: faster-whisper`, `device: cpu`, `compute_type: int8`. Works,
but `large-v3` is slow on CPU — consider `model: medium` for faster responses.

### 4c. AMD GPU path (Windows)

faster-whisper/ctranslate2 cannot target AMD GPUs (CUDA-only). Use the **whisper.cpp Vulkan engine**
this fork adds — see the *AMD GPU fork* section in [`README.md`](README.md) for the build + config steps.

### 5. First run

```bash
python run.py
```

A settings window opens; closing it leaves the app running in the background. For the
`faster-whisper` engine the first run downloads the model (~3 GB for `large-v3`) to
`~/.cache/huggingface`.

### 6. Launchers / autostart (optional)

- `start.bat` — start with a console (double-click).
- `start_hidden.vbs` — start hidden (no console window). Both derive their own folder, so no path editing is needed.
- Autostart at login: copy `start_hidden.vbs` into the folder that `shell:startup` opens.

## Verify without the GUI

```bash
# faster-whisper engine (loads a model, transcribes 1 s of silence -> no crash = OK):
python -c "import numpy as np; from faster_whisper import WhisperModel; m=WhisperModel('tiny',device='cpu',compute_type='int8'); list(m.transcribe(np.zeros(16000,dtype=np.float32),language='en')[0]); print('OK')"
```

For the whisper.cpp Vulkan engine, run `whisper-cli.exe -m ggml-large-v3.bin -f samples/jfk.wav` and
confirm the log shows your GPU (`ggml_vulkan: ... using Vulkan0 backend`), not a CPU fallback.

## Problems we hit (and the fix)

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | `pip install` fails, no wheels | system Python was too new (3.12+/3.13/3.14) | dedicated **Python 3.11** env |
| 2 | `Failed to build av` | `av==11.0.0` has no Windows/py3.11 wheel → source build (needs FFmpeg) | widened pin to **`av>=11,<13`** (already in `requirements.txt`) |
| 3 | `ModuleNotFoundError: pkg_resources` on `import ctranslate2` | setuptools ≥81 removed `pkg_resources` | **`pip install "setuptools<81"`** |
| 4 | Hard crash: `Could not locate cudnn_ops_infer64_8.dll` (NVIDIA) | `ctranslate2 4.2.1` too old for recent GPUs + cuDNN 8 missing | upgrade to **ct2 4.8 + faster-whisper 1.2 + cuDNN 9 / cuBLAS cu12** (step 4a) |

Common thread: the upstream repo is frozen on early-2024 pins; on a modern stack (newer Python,
setuptools, GPUs) each layer needs a small bump. Each fix is a one-liner.
