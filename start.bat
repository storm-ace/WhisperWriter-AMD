@echo off
rem WhisperWriter launcher — gebruikt de lokale venv (.venv) en de repo-map als werkmap.
cd /d "%~dp0"
".venv\Scripts\python.exe" run.py
pause
