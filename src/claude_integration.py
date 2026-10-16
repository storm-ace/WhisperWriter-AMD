"""Register / detect the WhisperWriter MCP server in Claude Code."""

import json
import os
import shutil
import subprocess
import sys

SERVER_NAME = 'whisperwriter'
MCP_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mcp_server.py')


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
    cmd = [_console_python(), MCP_SCRIPT]
    if db_path:
        cmd += ['--db', db_path]
    return cmd


def add_command(db_path=None):
    return ['claude', 'mcp', 'add', '--scope', 'user', SERVER_NAME, '--'] + server_command(db_path)


def add_command_text(db_path=None):
    return subprocess.list2cmdline(add_command(db_path))


def is_registered():
    """True if ~/.claude.json lists the server (user scope or any project scope)."""
    path = os.path.join(os.path.expanduser('~'), '.claude.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    if SERVER_NAME in (data.get('mcpServers') or {}):
        return True
    for project in (data.get('projects') or {}).values():
        if SERVER_NAME in ((project or {}).get('mcpServers') or {}):
            return True
    return False


def register(db_path=None):
    """Run `claude mcp add`. Returns (ok, message)."""
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
