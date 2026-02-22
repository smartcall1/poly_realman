$ErrorActionPreference = 'SilentlyContinue'
[console]::InputEncoding = [console]::OutputEncoding = New-Object System.Text.UTF8Encoding

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host " 🐋 POLYMARKET WHALE COPY SYSTEM START" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Whale Manager 백그라운드 무한 루프 래퍼 실행 (6시간마다 갱신)
Write-Host "[1] Starting Whale DB Manager (updates every 6 hours)..." -ForegroundColor Yellow
$manager_script = @"
while (`$true) {
    Write-Host '--- Running Whale Manager ---' -ForegroundColor Green
    python whale_manager.py
    Write-Host '--- Next update in 6 hours ---' -ForegroundColor Yellow
    Start-Sleep -Seconds 21600
}
"@
Start-Process powershell -ArgumentList "-NoExit -Command $manager_script" -WindowStyle Normal

Start-Sleep -Seconds 2

# 2. Whale Copy Bot 실행 (실시간 타겟 매매)
Write-Host "[2] Starting Real-time Whale Copy Bot..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit -Command python whale_copy_bot.py" -WindowStyle Normal

Write-Host ""
Write-Host "✅ 성공적으로 두 개의 봇 창이 열렸습니다!" -ForegroundColor Green
Write-Host "- 창 1: 6시간마다 고래 목록(whales.json)을 감시/업데이트하는 매니저"
Write-Host "- 창 2: 5초마다 고래들의 액션을 감시하여 카피하는 페이퍼 트레이더"
Write-Host ""
Write-Host "대시보드(dashboard.py)를 열어두시면 WhaleCopy 의 실시간 스탯을 볼 수 있습니다." -ForegroundColor Gray
