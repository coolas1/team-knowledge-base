# ============================================
# 团队知识库 RAG 平台 - 一键启动脚本
# ============================================

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 团队知识库 RAG 平台 - 启动中..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. 启动 Docker 基础设施
Write-Host "`n[1/3] 启动 Docker 基础设施..." -ForegroundColor Yellow
Push-Location $Root
docker compose up -d
if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 1) {
    Write-Host "  ✓ Docker 容器已启动" -ForegroundColor Green
} else {
    Write-Host "  ✗ Docker 启动失败，请检查 Docker Desktop 是否运行" -ForegroundColor Red
    Pop-Location
    exit 1
}
Pop-Location

# 等待数据库就绪
Write-Host "  等待数据库就绪..." -ForegroundColor Gray
Start-Sleep -Seconds 3

# 2. 启动后端服务（新窗口）
Write-Host "`n[2/3] 启动后端服务 (port 8000)..." -ForegroundColor Yellow
$backendCmd = "cd '$Root'; .venv\Scripts\python.exe -m uvicorn src.main:app --reload --host 127.0.0.1 --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd
Write-Host "  ✓ 后端服务已在新窗口启动" -ForegroundColor Green

# 3. 启动前端服务（新窗口）
Write-Host "`n[3/3] 启动前端服务 (Vite)..." -ForegroundColor Yellow
$frontendCmd = "cd '$Root\frontend'; npx vite"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd
Write-Host "  ✓ 前端服务已在新窗口启动" -ForegroundColor Green

# 完成
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host " 全部服务已启动！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  后端 API:  http://127.0.0.1:8000" -ForegroundColor White
Write-Host "  前端页面:  http://localhost:5173" -ForegroundColor White
Write-Host "  API 文档:  http://127.0.0.1:8000/docs" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "`n提示: 关闭对应的 PowerShell 窗口即可停止相应服务" -ForegroundColor Gray
