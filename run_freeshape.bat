@echo off
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo Python was not found. Install Python 3.12 or 3.13 from https://www.python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
%PY% start.py %*
if errorlevel 1 pause
