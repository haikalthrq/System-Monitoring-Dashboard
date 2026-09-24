@echo off
setlocal
cd /d "%~dp0"

set PORT=9090
if not "%~1"=="" set PORT=%~1

echo Starting System Monitor on port %PORT%...
python app.py --port %PORT% --host 0.0.0.0
pause
