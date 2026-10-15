@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Chat Documentation
if not exist ".venv\Scripts\python.exe" (
  echo First run setup.cmd, or install dependencies and run python program.py.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u program.py
pause
