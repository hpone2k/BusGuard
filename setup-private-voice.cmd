@echo off
set "BUSGUARD_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%BUSGUARD_PYTHON%" set "BUSGUARD_PYTHON=%~dp0..\vision4477\.venv\Scripts\python.exe"
if not exist "%BUSGUARD_PYTHON%" (
  echo No Python environment found. Follow README.md first.
  pause
  exit /b 1
)
"%BUSGUARD_PYTHON%" "%~dp0scripts\setup_voice.py"
pause
