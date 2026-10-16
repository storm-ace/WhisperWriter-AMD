"""
Detect the window that will receive the dictated text.

Used to label dictations ("→ Windows Terminal") and to switch on Claude Code-friendly
behaviour when the focused window is a terminal or editor running Claude Code.
Windows only (ctypes); other platforms return an empty target.
"""

import ctypes
import os
import re
import time
from ctypes import wintypes
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

# Claude Code sets the terminal title to "✳ <task>" (or a braille spinner while working),
# or the title contains "Claude Code" / starts with "claude" (the command line).
_CLAUDE_TITLE = re.compile(r'^\s*[✳✢✻✽⠀-⣿]|\bclaude code\b|^\s*claude\b',
                           re.IGNORECASE)

# Processes that indicate a running Claude Code CLI (native build, or the npm package on node).
_CLAUDE_PROCESSES = {'claude.exe', 'node.exe'}
_PROCESS_CACHE_S = 3.0
_process_cache = (0.0, frozenset())


def _running_process_names():
    """Lower-case executable names of all running processes (cached for a few seconds)."""
    global _process_cache
    now = time.monotonic()
    if now - _process_cache[0] < _PROCESS_CACHE_S:
        return _process_cache[1]
    names = set()
    if os.name == 'nt':
        try:
            kernel32 = ctypes.windll.kernel32

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                            ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_void_p),
                            ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                            ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', ctypes.c_long),
                            ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_wchar * 260)]

            TH32CS_SNAPPROCESS = 0x2
            snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                names.add(entry.szExeFile.lower())
                ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
            kernel32.CloseHandle(snap)
        except Exception:
            pass
    _process_cache = (now, frozenset(names))
    return _process_cache[1]


def claude_code_running():
    return bool(_CLAUDE_PROCESSES & _running_process_names())


@dataclass
class Target:
    """A top-level window that dictated text can go to."""
    title: str = ''
    exe: str = ''
    pid: int = 0
    hwnd: int = 0

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
        """A terminal/editor whose title looks like Claude Code, while a Claude process runs."""
        return (self.exe.lower() in TERMINAL_HOSTS
                and bool(_CLAUDE_TITLE.search(self.title))
                and claude_code_running())

    @property
    def label(self):
        """Short display label, e.g. 'Windows Terminal · Claude Code'."""
        if not self.app:
            return ''
        return f'{self.app} · Claude Code' if self.is_claude_code else self.app


def _window_target(hwnd):
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
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
    return Target(title=buf.value, exe=exe, pid=pid.value, hwnd=hwnd)


def foreground_target():
    if os.name != 'nt':
        return Target()
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        return _window_target(hwnd) if hwnd else Target()
    except Exception:
        return Target()


def find_claude_code_window():
    """The top-most visible window running Claude Code, or None."""
    if os.name != 'nt' or not claude_code_running():
        return None
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            target = _window_target(hwnd)
            if target.is_claude_code:
                found.append(target)
                return False   # EnumWindows walks top-down: the first match is the most recent
        return True

    try:
        user32.EnumWindows(visit, 0)
    except Exception:
        return None
    return found[0] if found else None


def focus_window(hwnd):
    """Bring a window to the front. Returns True when it is the foreground window."""
    if os.name != 'nt' or not hwnd:
        return False
    user32 = ctypes.windll.user32
    SW_RESTORE, VK_MENU, KEYEVENTF_KEYUP = 9, 0x12, 0x2
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows only lets the foreground process hand over focus; a synthetic Alt tap
    # satisfies that rule for SetForegroundWindow.
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    for _ in range(20):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.025)
    return False


_FILENAME = re.compile(
    r'(?<![`\w/\\.])((?:[\w\-]+[/\\])*[\w\-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|json|md|yaml|yml|'
    r'toml|ini|cfg|cs|csproj|sln|cpp|cc|c|h|hpp|go|rs|java|kt|swift|rb|php|sql|html|css|scss|'
    r'sh|ps1|bat|vue|svelte|dart|lua|xml|txt|env|lock|dockerfile))(?![`\w])',
    re.IGNORECASE)


def backtick_filenames(text):
    """Wrap bare file names in backticks: 'open auth_guard.py' -> 'open `auth_guard.py`'."""
    return _FILENAME.sub(r'`\1`', text)
