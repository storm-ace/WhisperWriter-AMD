@echo off
rem WhisperWriter launcher: uses the local venv (.venv) and this folder as working directory.
rem On the first start it creates .venv and installs the dependencies automatically.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto setup
if not exist ".venv\.deps-installed" goto install
goto run

:setup
echo First start: creating the Python environment (.venv)...
py -3.11 -m venv .venv 2>nul
if not exist ".venv\Scripts\python.exe" py -3.10 -m venv .venv 2>nul
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Could not create .venv. Install Python 3.11 ^(or 3.10^) from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH"^) and run start.bat again.
    pause
    exit /b 1
)

:install
echo Installing dependencies, this takes a few minutes the first time...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt "setuptools<81"
if errorlevel 1 (
    echo.
    echo Installing the dependencies failed, see the messages above.
    echo WhisperWriter needs Python 3.10 or 3.11: check with   .venv\Scripts\python.exe --version
    pause
    exit /b 1
)
echo ok> ".venv\.deps-installed"

:run
".venv\Scripts\python.exe" run.py
if errorlevel 1 (
    echo.
    echo WhisperWriter stopped with an error. Details: %APPDATA%\WhisperWriter\crash.log
    pause
)
