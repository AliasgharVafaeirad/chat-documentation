@echo off
cd /d "%~dp0"
py -3.13 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto failed
echo Setup complete. Open Chat Documentation.cmd.
pause
exit /b 0
:failed
echo Setup stopped. Install Python 3.13 and check the error above. See README.md.
pause
exit /b 1
