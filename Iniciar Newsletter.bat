@echo off
title Newsletter Central
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERRO] Ambiente virtual nao encontrado em .venv\
    echo Rode primeiro: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Newsletter Central
echo ============================================
echo   Servidor: http://127.0.0.1:8000
echo   Para parar: feche esta janela ou Ctrl+C
echo ============================================
echo.

REM Abre o navegador depois de 3s, em paralelo
start "" /b cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8000"

REM Roda o servidor (fica preso aqui ate fechar)
".venv\Scripts\python.exe" run.py

echo.
echo Servidor encerrado.
pause
