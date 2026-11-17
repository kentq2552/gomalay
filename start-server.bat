@echo off
rem Chay server tren may roi mo dashboard. Dong cua so nay de tat server.
cd /d "%~dp0"
start "" http://localhost:5055
".venv\Scripts\python.exe" app.py
