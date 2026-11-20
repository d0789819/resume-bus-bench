@echo off
rem Start the Bus Bench backend (http://127.0.0.1:8000). Close this window to stop it.
title Bus Bench server
cd /d "%~dp0..\.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" "%~dp0run_server.py"
echo.
echo Server stopped.
pause
