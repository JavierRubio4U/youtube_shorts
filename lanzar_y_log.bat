@echo off
cd /d "%~dp0"

:: Apuntamos a scripts\publish.py usando el python del entorno virtual
powershell -Command ".\venv\Scripts\python.exe scripts\publish.py | Tee-Object -FilePath log_ejecucion.txt"

pause