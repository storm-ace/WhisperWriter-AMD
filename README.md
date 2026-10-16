# WhisperWriter AMD

Local, GPU-accelerated speech-to-text for Windows that types wherever your cursor is — built for
**AMD GPUs** and for dictating prompts into **Claude Code**.

![version](https://img.shields.io/badge/version-2.0.0--amd-orange)
![python](https://img.shields.io/badge/python-3.11-blue)
![ui](https://img.shields.io/badge/UI-PySide6%20%2F%20Qt%206-41cd52)
![license](https://img.shields.io/badge/license-GPL--3.0-lightgrey)

Press a hotkey, talk, and the text lands in the focused window. Every dictation is saved to a
searchable local history, and Claude Code can look back through it over MCP. Nothing leaves
your machine.

- **Fast on AMD**: Whisper runs on your Radeon GPU or iGPU through
  [whisper.cpp](https://github.com/ggml-org/whisper.cpp) + Vulkan. `large-v3` runs at about
  10× realtime on a Radeon 8060S iGPU, against about 1.2× on the CPU.
- **Sessions**: dictations are grouped into sessions and stored in SQLite with full-text search.
- **Built for Claude Code**: it detects Claude Code in your terminal, never auto-submits the
  prompt, wraps file names in backticks, and gives Claude Code read access to your dictation
  history through an MCP server.
- **Modern UI**: a dark Qt 6 interface with a floating recorder that never steals focus, a
  Ctrl+K command palette, and settings that apply without a restart.

---

## Contents

- [Quick start](#quick-start)
- [AMD GPU setup (whisper.cpp + Vulkan)](#amd-gpu-setup-whispercpp--vulkan)
- [Using it](#using-it)
- [Claude Code integration](#claude-code-integration)
- [Sessions and history](#sessions-and-history)
- [Accuracy: vocabulary, replacements, LLM correction](#accuracy-vocabulary-replacements-llm-correction)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Credits and license](#credits-and-license)

## Quick start

Requirements: Windows 10/11, **Python 3.11** (3.12+ is not supported by the pinned
dependencies) and Git.

```bash
git clone https://github.com/storm-ace/WhisperWriter-AMD
cd WhisperWriter-AMD
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

On first launch the app opens on **Settings → Engine & GPU**. Pick an engine and click
**Save & apply**:

| Engine | Runs on | Choose it when |
| --- | --- | --- |
| **whisper.cpp · Vulkan** | AMD / Intel / NVIDIA GPU | You have an AMD GPU or iGPU. Requires the one-time build below. |
| **faster-whisper** | CPU (or CUDA on NVIDIA) | You want it working right away, with no extra build. |

After that, closing the window keeps WhisperWriter running in the system tray. Use
`start.bat` to launch with a console, `start_hidden.vbs` to launch without one, or
`create_shortcut.ps1` for an autostart shortcut. [INSTALL.md](INSTALL.md) covers Windows
pitfalls (setuptools, NVIDIA/CUDA, and so on).

## AMD GPU setup (whisper.cpp + Vulkan)

The stock `faster-whisper` engine runs on `ctranslate2`, which supports only CPU and NVIDIA
CUDA, so AMD GPUs sit idle. This fork runs Whisper through a local `whisper-server` built with
the Vulkan backend, which works on AMD discrete GPUs and iGPUs (tested on RDNA 3.5, `gfx1151`).

1. **Build whisper.cpp with Vulkan.** You need the [Vulkan SDK](https://vulkan.lunarg.com/),
   [CMake](https://cmake.org/), and MSVC or [MinGW-w64](https://winlibs.com/).

   ```bash
   git clone --branch v1.9.1 https://github.com/ggml-org/whisper.cpp
   cd whisper.cpp
   cmake -B build -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
   cmake --build build -j --config Release
   ```

   You need whisper.cpp **v1.8.1 or newer** for AMD iGPU detection over Vulkan.

2. **Download a ggml model.** It must be ggml format, not the faster-whisper format.

   ```bash
   curl -L -o models/ggml-large-v3.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin
   ```

   `ggml-large-v3-turbo.bin` is about twice as fast, with slightly lower accuracy.

3. **Point WhisperWriter at it.** In **Settings → Engine & GPU**, choose *whisper.cpp · Vulkan*
   and set **Server binary** (`build\bin\whisper-server.exe`) and **Model file**. Set
   **Extra DLL folder** to MinGW's `bin` folder only if you built with MinGW.

4. Click **Save & apply**. The status pill in the top bar changes to
   `Ready · whisper.cpp · Vulkan · large-v3`. WhisperWriter starts the server, keeps the model
   loaded, and stops the server when you quit from the tray.

## Using it

| Action | How |
| --- | --- |
| Dictate into any app | `Ctrl+Shift+Space` (configurable, mouse buttons work too) |
| Dictate without pressing Enter | Optional second hotkey (`activation_key_no_enter`) |
| Dictate into the current session only | Record button in the main window |
| Search sessions and run commands | `Ctrl+K` |
| New session | `Ctrl+N` |
| Search transcripts | `Ctrl+F` |
| Settings | `Ctrl+,` |

**Recording modes**, switchable in the record dock:

- **Continuous** (default): recording stops after a pause, then starts again. Press the
  hotkey again to stop.
- **Voice detect**: recording stops after a pause and does not restart.
- **Toggle**: press the hotkey once to start and once to stop.
- **Hold**: records while the hotkey is held down.

While you talk, a small pill shows the timer and a live waveform. It then shows the
transcription and where it was typed. The pill never takes keyboard focus, so the text always
lands in the window you were in.

## Claude Code integration

### Dictating prompts

When **Claude Code aware typing** is on (the default), WhisperWriter checks the focused window.
If it is a terminal or editor running Claude Code, WhisperWriter:

- never presses Enter, so you can review the prompt before sending it
- wraps file names in backticks (`open auth_guard.py` becomes ``open `auth_guard.py` ``)
- labels the dictation `Windows Terminal · Claude Code` in your history

### Letting Claude Code read your history (MCP)

WhisperWriter ships a small stdio MCP server with read-only access to your session database.
Register it once, either from **Settings → Claude Code → Add to Claude Code** or yourself:

```bash
claude mcp add --scope user whisperwriter -- python C:\path\to\WhisperWriter-AMD\src\mcp_server.py
```

Claude Code then gets these tools:

| Tool | What it does |
| --- | --- |
| `search_dictations` | Full-text search over every dictation |
| `list_sessions` | Recent sessions with id, title, time and size |
| `get_session` | Full transcript of one session (default: the latest) |
| `recent_dictations` | The latest dictations, optionally limited to the last N minutes |

Ask things like *"check my dictations from this morning about the auth refactor"* and Claude
Code can pull that context itself. The server uses only the standard library, opens the
database read-only, and works while the app is running.

To paste a session into a prompt by hand, use **Copy for Claude Code**. It copies the session
as a `<dictation-session>` context block.

## Sessions and history

- Every dictation is saved with its text, raw engine output, target app, audio length and
  latency.
- A new session starts automatically after 30 idle minutes (configurable), or when you click
  **New session**. **Continue here** sends new dictations to an older session.
- Rename a session by double-clicking its title. Right-click a session to copy, export it as
  Markdown, or delete it.
- Storage: `%APPDATA%\WhisperWriter\sessions.db` (SQLite with WAL and FTS5). To turn history
  off, set `sessions.enabled: false`.

## Accuracy: vocabulary, replacements, LLM correction

- **Project vocabulary** (`model_options.common.vocabulary`): names, file names and jargon,
  separated by commas. They are added to Whisper's initial prompt so they are spelled right the
  first time.
- **Word replacements**: deterministic `wrong = right` rules, one per line, applied after
  every transcription. They add no latency.
- **LLM correction** (off by default): passes each transcription through a local
  [llama.cpp](https://github.com/ggml-org/llama.cpp) server on the same GPU. It fixes
  punctuation, casing and clear mishearings without rewriting. It adds about 0.4–1 s. In
  testing, Gemma-3-4B-it (Q4_K_M GGUF) followed the "correct, don't rewrite" instruction best.
  Build llama.cpp with `-DGGML_VULKAN=ON` and set the paths under
  **Settings → Correction & vocabulary**. If the LLM fails or times out, the raw text is used.

## Configuration

Everything is editable in the app. Settings are stored in `src/config.yaml`, which is
git-ignored. [`config.yaml.example`](config.yaml.example) is an annotated starting point.
[`src/config_schema.yaml`](src/config_schema.yaml) defines every option, its default and its
description; the settings page is generated from it.

| Section | Highlights |
| --- | --- |
| `model_options` | `engine`, language, temperature, vocabulary, plus `whispercpp.*` and `local.*` engine settings |
| `recording_options` | hotkeys, recording mode, microphone, silence and minimum duration |
| `post_processing` | insert method (`clipboard` is the most reliable), Enter after dictating, replacements, `llm_correction.*` |
| `claude_code` | smart terminal mode, suppress Enter, backtick file names |
| `sessions` | enable history, idle split in minutes, database path |
| `misc` | hide the recorder pill, beep on completion, start in tray, log to terminal |

Saving applies changes in the running app. The engine and servers restart only when their own
settings changed.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Status shows `whisper.cpp server not running` | Check **Server binary** and **Model file** paths. With MinGW builds, set **Extra DLL folder**. Click **Restart engine**. |
| Server log shows a CPU fallback instead of `ggml_vulkan: ... AMD Radeon` | Update your GPU driver and use whisper.cpp ≥ v1.8.1. |
| Characters get dropped while typing | Set **Insert text with** to `clipboard`. |
| Hotkey does nothing | Another app may hold the shortcut. Choose a different combo or a mouse button. |
| The MCP tools don't show up in Claude Code | Restart the Claude Code session after registering. Run `claude mcp list` to check. |
| A second launch does nothing | By design: it brings the running instance to the front. Look in the tray. |

Report fork-specific issues (AMD engine, UI, sessions, MCP) on
[this repo](https://github.com/storm-ace/WhisperWriter-AMD/issues/new).

## Project layout

```text
run.py                    entry point (loads .env, starts src/main.py)
src/main.py               app wiring: engine manager, hotkeys, recording, tray
src/sessions.py           SQLite + FTS5 session store (no Qt dependency)
src/mcp_server.py         stdio MCP server for Claude Code (standard library only)
src/claude_integration.py MCP registration helpers
src/active_window.py      focused-window detection, Claude Code heuristics
src/transcription.py      whisper.cpp / faster-whisper engines, post-processing
src/result_thread.py      recording + VAD + transcription worker
src/ui/                   PySide6 UI: theme, main window, settings, overlay, palette
```

## Credits and license

- [savbell/whisper-writer](https://github.com/savbell/whisper-writer): the original
  WhisperWriter, which this project forks, and its
  [contributors](https://github.com/savbell/whisper-writer/graphs/contributors).
- [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp) and
  [llama.cpp](https://github.com/ggml-org/llama.cpp): the Vulkan-capable engines.
- [OpenAI Whisper](https://github.com/openai/whisper) and
  [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper).

Licensed under the **GNU General Public License v3.0**; see [LICENSE](LICENSE). This repository
is a modified version of savbell/whisper-writer. The changes are the whisper.cpp/Vulkan engine
(June 2026) and the PySide6 UI, session history and Claude Code integration (October 2026).
The upstream code remains © its respective authors.
