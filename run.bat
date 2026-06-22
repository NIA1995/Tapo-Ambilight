@echo off
rem Launch the Tapo Ambilight app with no console window.
cd /d "%~dp0"
start "" pythonw "app.py" %*
