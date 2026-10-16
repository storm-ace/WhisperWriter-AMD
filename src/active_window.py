"""
Detect the window that will receive the dictated text.

Used to label dictations ("→ Windows Terminal") and to switch on Claude Code-friendly
behaviour when the focused window is a terminal or editor running Claude Code.
Windows only (ctypes); other platforms return an empty target.
"""

import os
import re
from dataclasses import dataclass

FRIENDLY_NAMES = {
    'windowsterminal.exe': 'Windows Terminal',
    'wt.exe': 'Windows Terminal',
    'cmd.exe': 'Command Prompt',
    'powershell.exe': 'PowerShell',
    'pwsh.exe': 'PowerShell',
    'code.exe': 'VS Code',
    'cursor.exe': 'Cursor',
    'windsurf.exe': 'Windsurf',
    'idea64.exe': 'IntelliJ IDEA',
    'rider64.exe': 'Rider',
    'pycharm64.exe': 'PyCharm',
    'devenv.exe': 'Visual Studio',
    'wezterm-gui.exe': 'WezTerm',
    'alacritty.exe': 'Alacritty',
    'mintty.exe': 'Git Bash',
    'claude.exe': 'Claude',
    'chrome.exe': 'Chrome',
    'msedge.exe': 'Edge',
    'firefox.exe': 'Firefox',
    'ms-teams.exe': 'Teams',
    'teams.exe': 'Teams',
    'slack.exe': 'Slack',
    'outlook.exe': 'Outlook',
    'olk.exe': 'Outlook',
    'notion.exe': 'Notion',
    'obsidian.exe': 'Obsidian',
    'winword.exe': 'Word',
}

# Hosts where Claude Code (the CLI) can run.
TERMINAL_HOSTS = {
    'windowsterminal.exe', 'wt.exe', 'cmd.exe', 'powershell.exe', 'pwsh.exe', 'code.exe',
    'cursor.exe', 'windsurf.exe', 'idea64.exe', 'rider64.exe', 'pycharm64.exe',
    'wezterm-gui.exe', 'alacritty.exe', 'mintty.exe', 'conhost.exe', 'openconsole.exe',
}

# Claude Code sets the terminal title to "✳ <task>" (or a braille spinner while working).
_CLAUDE_TITLE = re.compile(r'claude|^[✳✢✻✽·⠀-⣿]', re.IGNORECASE)


@dataclass
class Target:
    title: str = ''
    exe: str = ''
    pid: int = 0

    @property
    def app(self):
        if not self.exe:
            return ''
        return FRIENDLY_NAMES.get(self.exe.lower(), os.path.splitext(self.exe)[0])

    @property
    def is_own_process(self):
        return self.pid == os.getpid()

    @property
    def is_claude_code(self):
        return self.exe.lower() in TERMINAL_HOSTS and bool(_CLAUDE_TITLE.search(self.title.strip()))

    @property
    def label(self):
        """Short display label, e.g. 'Windows Terminal · claude'."""
        if not self.app:
            return ''
        return f'{self.app} · Claude Code' if self.is_claude_code else self.app


def foreground_target():
    if os.name != 'nt':
        return Target()
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return Target()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        exe = ''
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if handle:
            try:
                size = wintypes.DWORD(1024)
                path = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    exe = os.path.basename(path.value)
            finally:
                kernel32.CloseHandle(handle)
        return Target(title=buf.value, exe=exe, pid=pid.value)
    except Exception:
        return Target()


_FILENAME = re.compile(
    r'(?<![`\w/\\.])((?:[\w\-]+[/\\])*[\w\-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|json|md|yaml|yml|'
    r'toml|ini|cfg|cs|csproj|sln|cpp|cc|c|h|hpp|go|rs|java|kt|swift|rb|php|sql|html|css|scss|'
    r'sh|ps1|bat|vue|svelte|dart|lua|xml|txt|env|lock|dockerfile))(?![`\w])',
    re.IGNORECASE)


def backtick_filenames(text):
    """Wrap bare file names in backticks: 'open auth_guard.py' -> 'open `auth_guard.py`'."""
    return _FILENAME.sub(r'`\1`', text)
