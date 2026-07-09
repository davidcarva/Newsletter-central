@echo off
title Reiniciar Newsletter Central
cd /d "%~dp0"

echo Parando servidor anterior (se houver)...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
ping -n 3 127.0.0.1 >nul

echo Iniciando novamente...
start "" "%~dp0Iniciar Newsletter.bat"
exit /b 0
