@echo off
chcp 65001 > nul
cd /d "%~dp0"

:: Apuntamos a scripts\publish.py usando el python del entorno virtual
:: Guardamos el log en UTF-8 con Out-File
powershell -Command "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; & { .\venv\Scripts\python.exe -u scripts/publish.py 2>&1 | ForEach-Object { \"$_\" } } | Tee-Object -FilePath log_ejecucion.txt"

pause