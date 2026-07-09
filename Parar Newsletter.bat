@echo off
title Parar Newsletter Central
echo.
echo Procurando servidor rodando na porta 8000...

set "ENCONTROU=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo Encerrando processo PID %%a ...
    taskkill /F /PID %%a >nul 2>&1
    set "ENCONTROU=1"
)

if "%ENCONTROU%"=="0" (
    echo Nenhum servidor rodando na porta 8000.
) else (
    echo Servidor encerrado.
)

echo.
ping -n 2 127.0.0.1 >nul
exit /b 0
