@echo off
rem Bus Bench step 2: find the Tektronix scope and test tools via server\app\scope\tek.py (UART 115200).
rem Probe: CH1 tip on P17, ground clip on GND. Output: tools\bench\logs\scope_probe.log
setlocal
cd /d "%~dp0..\.."
set "LOG=%~dp0logs"
if not exist "%LOG%" mkdir "%LOG%"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo.
set "SCOPE_IP="
set /p "SCOPE_IP=Scope IP [Enter = use SCOPE_RESOURCE in .env]: "
set "SCOPE_CH=CH1"
echo Running, please wait (about 30 s) ...
".venv\Scripts\python.exe" "%~dp0scope_probe.py" "%SCOPE_IP%" "%SCOPE_CH%" > "%LOG%\scope_probe.log" 2>&1
echo Finished. Log: %LOG%\scope_probe.log
timeout /t 5 >nul
