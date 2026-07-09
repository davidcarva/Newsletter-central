# Alternativa em PowerShell — mesma coisa do .bat
$Host.UI.RawUI.WindowTitle = "Newsletter Central"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "[ERRO] Ambiente virtual nao encontrado em .venv\" -ForegroundColor Red
    Write-Host "Rode primeiro: python -m venv .venv ; .\.venv\Scripts\pip install -r requirements.txt"
    Read-Host "Pressione Enter para sair"
    exit 1
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Newsletter Central" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Servidor: http://127.0.0.1:8000"
Write-Host "  Para parar: feche esta janela ou Ctrl+C"
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Abre o navegador depois de 3s em background
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 3
    Start-Process "http://127.0.0.1:8000"
} | Out-Null

# Roda o servidor (preso aqui ate fechar)
& ".\.venv\Scripts\python.exe" run.py

Write-Host ""
Write-Host "Servidor encerrado."
Read-Host "Pressione Enter para sair"
