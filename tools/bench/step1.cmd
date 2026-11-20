@echo off
rem Bus Bench step 1: unit tests + ESP32 serial boot log + network/VISA diagnostics.
rem All output goes to tools\bench\logs\. Does not modify any project file.
setlocal
cd /d "%~dp0..\.."
set "ROOT=%CD%"
set "LOG=%~dp0logs"
if not exist "%LOG%" mkdir "%LOG%"
del /q "%LOG%\step1.status" 2>nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "PY=%ROOT%\.venv\Scripts\python.exe"

echo [1/4] pytest ...
pushd "%ROOT%\server"
"%PY%" -m pytest -q > "%LOG%\pytest.log" 2>&1
popd

echo [2/4] docker compose ps ...
docker compose ps > "%LOG%\docker.log" 2>&1

echo [3/4] ESP32 serial boot log (resets the board, up to 50 s) ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0serial_boot.ps1" -Seconds 50 > "%LOG%\serial.log" 2>&1

echo [4/4] network + VISA diagnostics ...
"%PY%" "%~dp0diag.py" "%LOG%\serial.log" > "%LOG%\diag.log" 2>&1

echo DONE > "%LOG%\step1.status"
echo Finished. Logs are in %LOG%
timeout /t 5 >nul
