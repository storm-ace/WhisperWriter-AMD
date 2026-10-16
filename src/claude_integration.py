"""Register / detect the WhisperWriter MCP server in Claude Code."""

import json
import os
import shutil
import subprocess
import sys

from mcp_server import SERVER_NAME, TOOLS
from utils import APP_ROOT, IS_FROZEN

MCP_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mcp_server.py')
TOOL_NAMES = [tool['name'] for tool in TOOLS]
CLAUDE_CONFIG = os.path.join(os.path.expanduser('~'), '.claude.json')
_registered_cache = (None, False)   # (mtime of ~/.claude.json, result)


def _console_python():
    """The MCP server talks over stdout, so it must not run under pythonw.exe."""
    exe = sys.executable
    folder, name = os.path.split(exe)
    if name.lower() == 'pythonw.exe':
        candidate = os.path.join(folder, 'python.exe')
        if os.path.isfile(candidate):
            return candidate
    return exe


def server_command(db_path=None):
    if IS_FROZEN:   # the release build ships the server as its own console program
        cmd = [os.path.join(APP_ROOT, 'whisperwriter-mcp.exe')]
    else:
        cmd = [_console_python(), MCP_SCRIPT]
    if db_path:
        cmd += ['--db', db_path]
    return cmd


def add_command(db_path=None):
    return ['claude', 'mcp', 'add', '--scope', 'user', SERVER_NAME, '--'] + server_command(db_path)


def add_command_text(db_path=None):
    return subprocess.list2cmdline(add_command(db_path))


def is_registered():
    """True if ~/.claude.json lists the server (user scope or any project scope).

    The file can be several MB, so the answer is cached until the file changes.
    """
    global _registered_cache
    try:
        mtime = os.path.getmtime(CLAUDE_CONFIG)
    except OSError:
        return False
    if _registered_cache[0] == mtime:
        return _registered_cache[1]
    try:
        with open(CLAUDE_CONFIG, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    found = SERVER_NAME in (data.get('mcpServers') or {}) or any(
        SERVER_NAME in ((project or {}).get('mcpServers') or {})
        for project in (data.get('projects') or {}).values())
    _registered_cache = (mtime, found)
    return found


def register(db_path=None):
    """Run `claude mcp add` (blocking; call it off the UI thread). Returns (ok, message)."""
    claude = shutil.which('claude')
    if not claude:
        return False, 'The claude CLI was not found on PATH. Copy the command and run it in a terminal.'
    cmd = [claude] + add_command(db_path)[1:]
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=flags)
    except Exception as e:
        return False, f'Could not run claude: {e}'
    output = (res.stdout + res.stderr).strip()
    if res.returncode == 0:
        return True, 'Registered. Restart Claude Code sessions to pick up the whisperwriter tools.'
    if 'already exists' in output.lower():
        return True, 'Already registered in Claude Code.'
    return False, output or f'claude exited with code {res.returncode}'
