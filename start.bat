@echo off
rem WhisperWriter launcher: uses the local venv (.venv) and this folder as working directory.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The Python environment .venv was not found in %CD%.
    echo Create it first:
    echo     py -3.11 -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt "setuptools<81"
    pause
    exit /b 1
)
".venv\Scripts\python.exe" run.py
if errorlevel 1 (
    echo.
    echo WhisperWriter stopped with an error. Details: %APPDATA%\WhisperWriter\crash.log
    pause
)
