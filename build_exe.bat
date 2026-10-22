@echo off
rem ===========================================================================
rem  Builds FreeShape as a normal Windows program:
rem    dist\FreeShape\FreeShape.exe            the app (with its support files)
rem    dist\FreeShape-portable-r8.zip          the same, zipped - unzip anywhere and run
rem    dist\FreeShape-Setup-r8.exe             an installer (only if Inno Setup is installed)
rem  Double-click this file. The first build takes about 5-10 minutes.
rem ===========================================================================
setlocal
cd /d "%~dp0"

rem --- 1. the private Python environment (created the same way run_freeshape.bat does)
if exist ".venv\Scripts\python.exe" goto :have_venv
echo [FreeShape] Setting up Python first...
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY goto :no_python
%PY% -c "import sys; sys.path.insert(0, '.'); from freeshape.bootstrap import ensure_environment; ensure_environment()"
if errorlevel 1 goto :failed
:have_venv
set VPY=%~dp0.venv\Scripts\python.exe

rem --- 2. PyInstaller (the tool that packs Python programs into an .exe)
echo [FreeShape] Installing the packaging tool...
"%VPY%" -m pip install --quiet --upgrade "pyinstaller>=6.16"
if errorlevel 1 ( echo Could not install PyInstaller - check your internet connection. & pause & exit /b 1 )

rem --- 3. build, then check the exe actually starts its geometry engine
set FREESHAPE_FULL=
call :build || goto :failed
call :selftest
if errorlevel 1 (
  echo [FreeShape] The quick build is missing something - building again with every library included...
  set FREESHAPE_FULL=1
  call :build || goto :failed
  call :selftest || goto :failed
)
echo [FreeShape] dist\FreeShape\FreeShape.exe works.

rem --- 4. a portable zip
echo [FreeShape] Making the portable zip...
if exist "dist\FreeShape-portable-r8.zip" del "dist\FreeShape-portable-r8.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\FreeShape' -DestinationPath 'dist\FreeShape-portable-r8.zip'"

rem --- 5. an installer, if Inno Setup is installed (free: https://jrsoftware.org/isdl.php)
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe
if defined ISCC (
  echo [FreeShape] Building the installer...
  "%ISCC%" /Q "packaging\FreeShape.iss"
  if errorlevel 1 ( echo The installer build failed - the app itself is still in dist\FreeShape. ) else ( echo [FreeShape] Installer: dist\FreeShape-Setup-r8.exe )
) else (
  echo [FreeShape] Skipped the installer: Inno Setup isn't installed. Get it free from https://jrsoftware.org/isdl.php
  echo             and run this again if you want a FreeShape-Setup.exe with Start-menu and desktop shortcuts.
)
echo.
echo Done! Your FreeShape app is in the dist folder.
explorer dist
pause
exit /b 0

:build
echo [FreeShape] Packing FreeShape into an .exe (this takes a few minutes)...
"%VPY%" -m PyInstaller packaging\freeshape.spec --noconfirm --clean --distpath dist --workpath build
exit /b %errorlevel%

:selftest
if exist "%TEMP%\freeshape_selftest.txt" del "%TEMP%\freeshape_selftest.txt"
start "" /wait "dist\FreeShape\FreeShape.exe" --self-test "%TEMP%\freeshape_selftest.txt"
findstr /b "OK" "%TEMP%\freeshape_selftest.txt" >nul 2>nul
if errorlevel 1 (
  if exist "%TEMP%\freeshape_selftest.txt" type "%TEMP%\freeshape_selftest.txt"
  exit /b 1
)
exit /b 0

:no_python
echo Python was not found - install it from https://www.python.org (tick "Add python.exe to PATH").
pause
exit /b 1

:failed
echo.
echo The build failed - scroll up for the error, or send a screenshot of this window.
pause
exit /b 1
