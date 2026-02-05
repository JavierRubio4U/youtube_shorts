@echo off
chcp 65001 > nul
cd /d "%~dp0"

:: Limpiamos el log antes de empezar
if exist log_ejecucion.txt del log_ejecucion.txt

:: Usamos PowerShell para ejecutar el script y redirigir la salida
:: Forzamos UTF8 sin BOM si es posible, o al menos consistente
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$OutputEncoding = [System.Text.Encoding]::UTF8; " ^
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " ^
    "& { .\venv\Scripts\python.exe -u scripts/publish.py 2>&1 } | ForEach-Object { " ^
    "    $line = $_.ToString(); " ^
    "    Write-Host $line; " ^
    "    $line | Out-File -FilePath log_ejecucion.txt -Append -Encoding UTF8 " ^
    "}"

pause