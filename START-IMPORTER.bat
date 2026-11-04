@echo off
title Strata Universal Model Importer
cd /d "%~dp0"

set "PYTHON_EXE="

:: 1. Check parent or local venv
if exist "..\Strata\.venv\Scripts\python.exe" set "PYTHON_EXE=..\Strata\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

:: 2. Check system Python
if not defined PYTHON_EXE (
  for %%P in (python.exe py.exe) do (
    if not defined PYTHON_EXE (
      %%P -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
      if not errorlevel 1 set "PYTHON_EXE=%%P"
    )
  )
)

if not defined PYTHON_EXE (
  echo ========================================================
  echo [Error] Python 3.10+ was not found on your system.
  echo Please install Python 3.10+ or add it to PATH.
  echo ========================================================
  pause
  exit /b 1
)

:: Launch GUI directly
start "" "%PYTHON_EXE%" gui.py
