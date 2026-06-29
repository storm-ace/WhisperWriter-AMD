# <img src="./assets/ww-logo.png" alt="WhisperWriter icon" width="25" height="25"> WhisperWriter

![version](https://img.shields.io/badge/version-1.0.8--amd-blue)

<p align="center">
    <img src="./assets/ww-demo-image-02.gif" alt="WhisperWriter demo gif" width="340" height="136">
</p>

---

## 🟢 AMD GPU fork (whisper.cpp + Vulkan)

This is a fork of [savbell/whisper-writer](https://github.com/savbell/whisper-writer) that adds a
**GPU transcription engine for AMD GPUs and integrated GPUs on Windows**, where the stock
`faster-whisper` engine cannot help.

**Why:** WhisperWriter transcribes with `faster-whisper`, which runs on `ctranslate2` — and that
engine only supports **CPU and NVIDIA CUDA**. There is no ROCm/DirectML path on Windows, so AMD
users were stuck on slow CPU transcription. This fork adds a third engine that runs Whisper through
a local [**whisper.cpp**](https://github.com/ggml-org/whisper.cpp) server built with the **Vulkan**
backend, which *does* run on AMD GPUs — including iGPUs.

**Result:** on an AMD Radeon 8060S iGPU (Ryzen AI Max / Strix Halo, RDNA 3.5, `gfx1151`), full
`large-v3` runs at roughly **~10× realtime** (≈1 s of compute for ~10 s of speech, model kept
resident), versus ~1.2× realtime on CPU — at full `large-v3` quality.

### What this fork adds
- A new `model_options.engine` setting: **`faster-whisper`** (original CPU engine) or **`whispercpp`**
  (this fork's GPU engine, via a local whisper.cpp HTTP server).
- `src/whispercpp_server.py`: auto-starts/stops a `whisper-server` subprocess and keeps the model resident.
- A tray icon that shows the active engine and whether the GPU server is running, plus a clean exit
  that also stops the server.

### Setup (Windows + AMD GPU)
1. **Install WhisperWriter** as below (steps under *Getting Started*), using Python 3.11 + a venv.
2. **Build whisper.cpp with Vulkan** (or use a community build). You need: the
   [Vulkan SDK](https://vulkan.lunarg.com/), [CMake](https://cmake.org/), and a C++ toolchain (MSVC or
   [MinGW-w64](https://winlibs.com/)). Then:
   ```
   git clone --branch v1.9.1 https://github.com/ggml-org/whisper.cpp
   cd whisper.cpp
   cmake -B build -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
   cmake --build build -j --config Release
   ```
   > Note: whisper.cpp **v1.8.1+** is required for AMD iGPU detection over Vulkan (v1.8.0 was broken).
3. **Download a ggml model** (note: ggml format, not the faster-whisper format):
   ```
   curl -L -o whisper.cpp/models/ggml-large-v3.bin \
     https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin
   ```
4. **Configure** `src/config.yaml` to use the engine (see [`config.yaml.example`](config.yaml.example)):
   ```yaml
   model_options:
     engine: whispercpp
     whispercpp:
       binary_path: C:\path\to\whisper.cpp\build\bin\whisper-server.exe
       model_path: C:\path\to\whisper.cpp\models\ggml-large-v3.bin
       lib_path: C:\path\to\mingw64\bin   # only if you built with MinGW (for libstdc++/libgomp DLLs)
   ```
5. **Run** `python run.py`. Verify the server log shows your AMD device
   (`ggml_vulkan: ... AMD Radeon ...` and `using Vulkan0 backend`), not a CPU fallback.

To switch back to CPU, set `engine: faster-whisper` and restart.

---

**Update (2024-05-28):** I've just merged in a major rewrite of WhisperWriter! We've migrated from using `tkinter` to using `PyQt5` for the UI, added a new settings window for configuration, a new continuous recording mode, support for a local API, and more! Please be patient as I work out any bugs that may have been introduced in the process. If you encounter any problems, please [open a new issue](https://github.com/savbell/whisper-writer/issues)!

WhisperWriter is a small speech-to-text app that uses [OpenAI's Whisper model](https://openai.com/research/whisper) to auto-transcribe recordings from a user's microphone to the active window.

Once started, the script runs in the background and waits for a keyboard shortcut to be pressed (`ctrl+shift+space` by default). When the shortcut is pressed, the app starts recording from your microphone. There are four recording modes to choose from:
- `continuous` (default): Recording will stop after a long enough pause in your speech. The app will transcribe the text and then start recording again. To stop listening, press the keyboard shortcut again.
- `voice_activity_detection`: Recording will stop after a long enough pause in your speech. Recording will not start until the keyboard shortcut is pressed again.
- `press_to_toggle` Recording will stop when the keyboard shortcut is pressed again. Recording will not start until the keyboard shortcut is pressed again.
- `hold_to_record` Recording will continue until the keyboard shortcut is released. Recording will not start until the keyboard shortcut is held down again.

You can change the activation shortcut (`activation_key`) — a keyboard combo or a mouse button — and the recording mode in the [Configuration Options](#configuration-options). While recording and transcribing, a small status window is displayed that shows the current stage of the process (but this can be turned off). Once the transcription is complete, the transcribed text will be automatically written to the active window.

Transcription runs locally. This fork adds a GPU engine for AMD via [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (Vulkan); the original CPU engine ([faster-whisper](https://github.com/SYSTRAN/faster-whisper/)) is also available. Choose the engine in the [Configuration Options](#configuration-options). (The OpenAI-API transcription option from upstream has been removed in this fork.)

**Fun fact:** Almost the entirety of the initial release of the project was pair-programmed with [ChatGPT-4](https://openai.com/product/gpt-4) and [GitHub Copilot](https://github.com/features/copilot) using VS Code. Practically every line, including most of this README, was written by AI. After the initial prototype was finished, WhisperWriter was used to write a lot of the prompts as well!

## Getting Started

### Prerequisites
Before you can run this app, you'll need to have the following software installed:

- Git: [https://git-scm.com/downloads](https://git-scm.com/downloads)
- Python `3.11`: [https://www.python.org/downloads/](https://www.python.org/downloads/)

If you want to run `faster-whisper` on your GPU, you'll also need to install the following NVIDIA libraries:

- [cuBLAS for CUDA 12](https://developer.nvidia.com/cublas)
- [cuDNN 8 for CUDA 12](https://developer.nvidia.com/cudnn)

<details>
<summary>More information on GPU execution</summary>

The below was taken directly from the [`faster-whisper` README](https://github.com/SYSTRAN/faster-whisper?tab=readme-ov-file#gpu):

**Note:** The latest versions of `ctranslate2` support CUDA 12 only. For CUDA 11, the current workaround is downgrading to the `3.24.0` version of `ctranslate2` (This can be done with `pip install --force-reinsall ctranslate2==3.24.0`).

There are multiple ways to install the NVIDIA libraries mentioned above. The recommended way is described in the official NVIDIA documentation, but we also suggest other installation methods below.

#### Use Docker

The libraries (cuBLAS, cuDNN) are installed in these official NVIDIA CUDA Docker images: `nvidia/cuda:12.0.0-runtime-ubuntu20.04` or `nvidia/cuda:12.0.0-runtime-ubuntu22.04`.

#### Install with `pip` (Linux only)

On Linux these libraries can be installed with `pip`. Note that `LD_LIBRARY_PATH` must be set before launching Python.

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

export LD_LIBRARY_PATH=`python3 -c 'import os; import nvidia.cublas.lib; import nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ":" + os.path.dirname(nvidia.cudnn.lib.__file__))'`
```

**Note**: Version 9+ of `nvidia-cudnn-cu12` appears to cause issues due its reliance on cuDNN 9 (Faster-Whisper does not currently support cuDNN 9). Ensure your version of the Python package is for cuDNN 8.

#### Download the libraries from Purfview's repository (Windows & Linux)

Purfview's [whisper-standalone-win](https://github.com/Purfview/whisper-standalone-win) provides the required NVIDIA libraries for Windows & Linux in a [single archive](https://github.com/Purfview/whisper-standalone-win/releases/tag/libs). Decompress the archive and place the libraries in a directory included in the `PATH`.

</details>

### Installation
To set up and run the project, follow these steps:

#### 1. Clone the repository:

```
git clone https://github.com/savbell/whisper-writer
cd whisper-writer
```

#### 2. Create a virtual environment and activate it:

```
python -m venv venv

# For Linux and macOS:
source venv/bin/activate

# For Windows:
venv\Scripts\activate
```

#### 3. Install the required packages:

```
pip install -r requirements.txt
```

#### 4. Run the Python code:

```
python run.py
```

#### 5. Configure and start WhisperWriter:
On first run, a Settings window should appear. Once configured and saved, another window will open. Press "Start" to activate the keyboard listener. Press the activation key (`ctrl+shift+space` by default) to start recording and transcribing to the active window.

### Configuration Options

WhisperWriter uses a configuration file to customize its behaviour. To set up the configuration, open the Settings window:

<p align="center">
    <img src="./assets/ww-settings-demo.gif" alt="WhisperWriter Settings window demo gif" width="350" height="350">
</p>

#### Model Options
- `engine`: Transcription engine. `whispercpp` = GPU via Vulkan (AMD GPUs/iGPUs; requires a whisper.cpp Vulkan build + ggml model — see the AMD GPU fork section above). `faster-whisper` = in-process CPU. (Default: `faster-whisper`)
- `common`: Options shared by both engines.
  - `language`: The language code for the transcription in [ISO-639-1 format](https://en.wikipedia.org/wiki/List_of_ISO_639_language_codes). (Default: `null`)
  - `temperature`: Controls the randomness of the transcription output. Lower values make the output more focused and deterministic. (Default: `0.0`)
  - `initial_prompt`: A string used as an initial prompt to bias the transcription (e.g. domain terms). (Default: `null`)

- `whispercpp`: Options for the whisper.cpp GPU (Vulkan) engine.
  - `binary_path`: Full path to `whisper-server.exe` (whisper.cpp built with Vulkan).
  - `model_path`: Full path to the ggml model file (e.g. `ggml-large-v3.bin`; ggml format, not the faster-whisper format).
  - `lib_path`: Directory with extra runtime DLLs the server needs (e.g. the MinGW `bin` dir); leave empty for MSVC builds.
  - `host` / `port`: Where the local server listens. (Defaults: `127.0.0.1` / `8080`)
  - `n_threads`: CPU threads for the server. (Default: empty)
  - `auto_start`: Let WhisperWriter launch/stop the server automatically. (Default: `true`)

- `local`: Options for the faster-whisper (CPU) engine.
  - `model`: The model to use. Larger models are more accurate but slower. See [available models](https://github.com/openai/whisper?tab=readme-ov-file#available-models-and-languages). (Default: `base`)
  - `device`: `cpu` only on this AMD build (ctranslate2 has no AMD GPU path; for GPU use the `whispercpp` engine). (Default: `cpu`)
  - `compute_type`: The compute type. [More on quantization](https://opennmt.net/CTranslate2/quantization.html). (Default: `default`)
  - `condition_on_previous_text`: Use the previously transcribed text as a prompt for the next request. (Default: `true`)
  - `vad_filter`: Use [a voice activity detection (VAD) filter](https://github.com/snakers4/silero-vad) to remove silence. (Default: `false`)
  - `model_path`: Path to a local model. If empty, the model is downloaded. (Default: `null`)

#### Recording Options
- `activation_key`: Shortcut to activate recording. A keyboard combo joined with `+` (e.g. `ctrl+shift+space`), or a mouse button: `mouse_back`, `mouse_forward`, `mouse_left`, `mouse_right`, `mouse_middle`. (Default: `ctrl+shift+space`)
- `activation_key_no_enter`: Optional second shortcut that dictates WITHOUT pressing Enter afterwards (same format as `activation_key`, e.g. `mouse_forward`). Leave empty to disable. (Default: `null`)
- `input_backend`: The input backend to use for detecting key presses. `auto` will try to use the best available backend. (Default: `auto`)
- `recording_mode`: The recording mode to use. Options include `continuous` (auto-restart recording after pause in speech until activation key is pressed again), `voice_activity_detection` (stop recording after pause in speech), `press_to_toggle` (stop recording when activation key is pressed again), `hold_to_record` (stop recording when activation key is released). (Default: `continuous`)
- `sound_device`: Input microphone. In the Settings window this is a dropdown of available microphones; in the config it is stored as the device name (or a numeric index), or empty for the system default. (Default: `null`)
- `sample_rate`: The sample rate in Hz to use for recording. (Default: `16000`)
- `silence_duration`: The duration in milliseconds to wait for silence before stopping the recording. (Default: `900`)
- `min_duration`: The minimum duration in milliseconds for a recording to be processed. Recordings shorter than this will be discarded. (Default: `100`)

#### Post-processing Options
- `writing_key_press_delay`: The delay in seconds between each key press when writing the transcribed text. (Default: `0.005`)
- `remove_trailing_period`: Set to `true` to remove the trailing period from the transcribed text. (Default: `false`)
- `add_trailing_space`: Set to `true` to add a space to the end of the transcribed text. (Default: `true`)
- `remove_capitalization`: Set to `true` to convert the transcribed text to lowercase. (Default: `false`)
- `input_method`: The method to use for simulating keyboard input. (Default: `pynput`)

#### Miscellaneous Options
- `print_to_terminal`: Set to `true` to print the script status and transcribed text to the terminal. (Default: `true`)
- `hide_status_window`: Set to `true` to hide the status window during operation. (Default: `false`)
- `noise_on_completion`: Set to `true` to play a noise after the transcription has been typed out. (Default: `false`)

If any of the configuration options are invalid or not provided, the program will use the default values.

## Known Issues

- For problems with **this fork's AMD GPU engine** (whisper.cpp / Vulkan / the `whispercpp` engine), please [open an issue on this repo](https://github.com/storm-ace/WhisperWriter-AMD/issues/new).
- For general WhisperWriter behaviour shared with upstream, see the [upstream issue tracker](https://github.com/savbell/whisper-writer/issues).

## Roadmap
Below are features I am planning to add in the near future:
- [x] Restructuring configuration options to reduce redundancy
- [x] Update to use the latest version of the OpenAI API
- [ ] Additional post-processing options:
  - [ ] Simple word replacement (e.g. "gonna" -> "going to" or "smiley face" -> "😊")
  - [ ] Using GPT for instructional post-processing
- [x] Updating GUI
- [ ] Creating standalone executable file

Below are features not currently planned:
- [ ] Pipelining audio files

Implemented features can be found in the [CHANGELOG](CHANGELOG.md).

## Contributing

Contributions are welcome:
- **This fork** (AMD GPU engine, whisper.cpp/Vulkan integration): [open a pull request](https://github.com/storm-ace/WhisperWriter-AMD/pulls) or [issue](https://github.com/storm-ace/WhisperWriter-AMD/issues/new) here.
- **General WhisperWriter** features/fixes that aren't AMD-specific are best contributed to the [upstream project](https://github.com/savbell/whisper-writer), so everyone benefits.

## Credits

- [savbell/whisper-writer](https://github.com/savbell/whisper-writer) — the original WhisperWriter, which this is a fork of.
- [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp) — the Vulkan-capable Whisper engine this fork adds.
- [OpenAI](https://openai.com/) for the Whisper model.
- [Guillaume Klein](https://github.com/guillaumekln) / [SYSTRAN](https://github.com/SYSTRAN/faster-whisper) for the faster-whisper package.
- All of the original [contributors](https://github.com/savbell/whisper-writer/graphs/contributors).

## License

This project is licensed under the **GNU General Public License v3.0** (GPL-3.0). See the [LICENSE](LICENSE) file for the full text.

This repository is a **modified version** of [savbell/whisper-writer](https://github.com/savbell/whisper-writer). The modifications — chiefly the whisper.cpp + Vulkan GPU engine for AMD GPUs/iGPUs — were added in June 2026. As required by the GPL, this fork remains licensed under GPL-3.0 and preserves the original license and authorship; the upstream code is © its respective WhisperWriter authors.
