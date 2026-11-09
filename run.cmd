@echo off
setlocal
"%~dp0.venv\Scripts\python.exe" "%~dp0translate_srt.py" %*
