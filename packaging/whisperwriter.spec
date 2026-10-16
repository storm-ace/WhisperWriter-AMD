# PyInstaller spec for the portable Windows release. Build with packaging\build_release.ps1.
#
# Two programs:
#   WhisperWriter.exe       the app (windowed, one folder)
#   whisperwriter-mcp.exe   the MCP server for Claude Code (console, single file: it talks
#                           over stdout, which a windowed program does not have)
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))
SRC = os.path.join(ROOT, 'src')
ICON = os.path.join(ROOT, 'assets', 'whisperwriter.ico')
HOOKS = [os.path.join(SPECPATH, 'hooks')]

# The release ships the whisper.cpp engine; faster-whisper (CPU/CUDA) and its large
# dependencies stay a source-install option.
EXCLUDES = ['faster_whisper', 'ctranslate2', 'onnxruntime', 'av', 'tokenizers',
            'huggingface_hub', 'numba', 'llvmlite', 'sympy', 'tiktoken', 'aiohttp',
            'tkinter', 'PyQt5', 'PIL', 'matplotlib', 'IPython', 'pytest']

app = Analysis(
    [os.path.join(SRC, 'main.py')],
    pathex=[SRC],
    datas=[(os.path.join(SRC, 'config_schema.yaml'), '.')],
    hiddenimports=['pynput.keyboard._win32', 'pynput.mouse._win32', 'audioplayer'],
    excludes=EXCLUDES,
    hookspath=HOOKS,
)
app_pyz = PYZ(app.pure)
app_exe = EXE(app_pyz, app.scripts, [], exclude_binaries=True, name='WhisperWriter',
              console=False, icon=ICON)
COLLECT(app_exe, app.binaries, app.datas, name='WhisperWriter')

mcp = Analysis(
    [os.path.join(SRC, 'mcp_server.py')],
    pathex=[SRC],
    excludes=EXCLUDES + ['PySide6', 'numpy', 'yaml', 'sounddevice', 'soundfile', 'requests'],
    hookspath=HOOKS,
)
mcp_pyz = PYZ(mcp.pure)
EXE(mcp_pyz, mcp.scripts, mcp.binaries, mcp.datas, name='whisperwriter-mcp',
    console=True, icon=ICON)
