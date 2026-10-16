# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [2.0.0-amd] - 2026-10-16
### Added
- Completely new PySide6 (Qt 6) interface: one main window with session history, live
  record dock, Claude Code context panel, Ctrl+K command palette and in-window settings.
- Session history: every dictation is stored in a local SQLite database with full-text
  search (FTS5); sessions split automatically after an idle gap or manually.
- MCP server (`src/mcp_server.py`) so Claude Code can search and read past dictations.
- Claude Code aware typing: detects Claude Code in the focused terminal, never presses
  Enter there and wraps file names in backticks.
- Project vocabulary, fed to Whisper as part of the initial prompt.
- Floating recorder pill with live waveform that never steals focus.
- "Send last dictation" and "Send session" (plus a send icon per dictation): paste into the
  most recent Claude window (desktop app or Claude Code terminal) and submit right away
  (`claude_code.submit_on_send`, on by default).
- Microphone picker in the record dock, crash log (`%APPDATA%\WhisperWriter\crash.log`), app
  icon, and an autostart shortcut (`create_shortcut.ps1 -Autostart`).
- Session summaries with the local LLM, stored with the session and exposed over MCP.
- Appearance settings: dark, light or system theme and four accent colours.
- Real GPU detection: the engine card shows the device whisper-server picked.

### Changed (continuous mode)
- The microphone stays open while a segment is transcribed, so nothing said meanwhile is lost.
- Stop (■, record button or hotkey) now ends continuous mode; cancel and stop never block the UI.
- Claude Code is detected by window title plus a running Claude process.

### Changed
- Settings apply in-process; only the engine/servers whose settings changed restart.
- Engine start-up runs in the background, so the window opens instantly.
- `faster-whisper` is imported lazily (not loaded at all for the whisper.cpp engine).
- A second launch now brings the running instance to the front instead of exiting silently.

### Fixed
- The hotkey listener was started again after every transcription, stacking listeners.

### Removed
- PyQt5 UI (old main, settings and status windows) and the restart-on-save flow.

## [Unreleased — upstream]
### Added
- New settings window to configure WhisperWriter.
- New main window to either start the keyboard listener or open the settings window.
- New continuous recording mode ([Issue #40](https://github.com/savbell/whisper-writer/issues/40)).
- New option to play a sound when transcription finishes ([Issue #40](https://github.com/savbell/whisper-writer/issues/40)).

### Changed
- Migrated status window from using `tkinter` to `PyQt5`.
- Migrated from using JSON to using YAML to store configuration settings.
- Upgraded to latest versions of `openai` and `faster-whisper`, including support for local API ([Issue #32](https://github.com/savbell/whisper-writer/issues/32)).

### Removed
- No longer using `keyboard` package to listen for key presses.

## [1.0.1] - 2024-01-28
### Added
- New message to identify whether Whisper was being called using the API or running locally.
- Additional hold-to-talk ([PR #28](https://github.com/savbell/whisper-writer/pull/28)) and press-to-toggle recording methods ([Issue #21](https://github.com/savbell/whisper-writer/issues/21)).
- New configuration options to:
  - Choose recording method (defaulting to voice activity detection).
  - Choose which sound device and sample rate to use.
  - Hide the status window ([PR #28](https://github.com/savbell/whisper-writer/pull/28)).

### Changed
- Migrated from `whisper` to `faster-whisper` ([Issue #11](https://github.com/savbell/whisper-writer/issues/11)).
- Migrated from `pyautogui` to `pynput` ([PR #10](https://github.com/savbell/whisper-writer/pull/10)).
- Migrated from `webrtcvad` to `webrtcvad-wheels` ([PR #17](https://github.com/savbell/whisper-writer/pull/17)).
- Changed default activation key combo from `ctrl+alt+space` to `ctrl+shift+space`.
- Changed to using a local model rather than the API by default.
- Revamped README.md, including new Roadmap, Contributing, and Credits sections.

### Fixed
- Local model is now only loaded once at start-up, rather than every time the activation key combo was pressed.
- Default configuration now auto-chooses compute type for the local model to avoid warnings.
- Graceful degradation to CPU if CUDA isn't available ([PR #30](https://github.com/savbell/whisper-writer/pull/30)).
- Removed long prefix of spaces in transcription ([PR #19](https://github.com/savbell/whisper-writer/pull/19)).

## [1.0.0] - 2023-05-29
### Added
- Initial release of WhisperWriter.
- Added CHANGELOG.md.
- Added Versioning and Known Issues to README.md.

### Changed
- Updated Whisper Python package; the local model is now compatible with Python 3.11.

[Unreleased]: https://github.com/savbell/whisper-writer/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/savbell/whisper-writer/releases/tag/v1.0.0...v1.0.1
[1.0.0]: https://github.com/savbell/whisper-writer/releases/tag/v1.0.0
