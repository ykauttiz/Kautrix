@echo off
title Kautrix - Mod Manager
rem Double-click to open the mod manager window. It manages <port>\mods and never touches the game package.
setlocal
cd /d "%~dp0"
set "PY="
for %%V in (3.14 3.13 3.12 3) do (
  if not defined PY (
    py -%%V -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  where python >nul 2>nul && (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul && set "PY=python"
  )
)
if not defined PY (
  echo Python 3.12 or newer is needed: https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)
%PY% -I "%~dp0tools\leonida_mods.py" gui %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
