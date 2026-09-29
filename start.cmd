@echo off
cd /d "%~dp0"
set "VISION_PYTHON=.venv\Scripts\python.exe"
if not exist "%VISION_PYTHON%" set "VISION_PYTHON=..\vision4477\.venv\Scripts\python.exe"
if not exist "%VISION_PYTHON%" (
  echo Project environment missing. Please follow README.md setup instructions.
  pause
  exit /b 1
)
"%VISION_PYTHON%" run.py %*
if errorlevel 1 pause
