# Web Campus PowerShell Launcher
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  Starting Web Campus Attendance Application..." -ForegroundColor Yellow
Write-Host "===================================================" -ForegroundColor Cyan
Set-Location -Path $PSScriptRoot
Start-Process "http://localhost:8080"
python server.py 8080
