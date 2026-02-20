$Strategies = @(
    @{ Name = "Theta_Reaper"; Edge = 0.0; Kelly = 0.10; Vol = 1.0; Alpha = 0.00 },
    @{ Name = "OB_Surfer"; Edge = -0.01; Kelly = 0.20; Vol = 1.2; Alpha = 0.20 },
    @{ Name = "Micro_Flash"; Edge = 0.0; Kelly = 0.15; Vol = 1.0; Alpha = 0.50 },
    @{ Name = "Spread_Arbit"; Edge = 0.05; Kelly = 0.15; Vol = 1.3; Alpha = 0.10 },
    @{ Name = "Bal_Factory"; Edge = 0.04; Kelly = 0.12; Vol = 1.2; Alpha = 0.25 }
)

Write-Host "🚀 트레이딩 군단출격" -ForegroundColor Cyan

# [INFO] 거래 내역 누적 모드 (사용자 요청: 히스토리 이어서 쌓기)
# $HistoryFile = "trade_history.jsonl"
# if (Test-Path $HistoryFile) {
#     if ($null -ne (Get-Content $HistoryFile -ErrorAction SilentlyContinue)) { 
#         $Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
#         $BackupFile = "trade_history_backup_$Timestamp.jsonl"
#         Move-Item -Path $HistoryFile -Destination $BackupFile
#         Write-Host "  📦 기존 거래 내역을 백업했습니다: $BackupFile" -ForegroundColor Gray
#     } else {
#         Remove-Item $HistoryFile
#     }
# }

# 기존 상태 파일 정리
Get-ChildItem "status_*.json" | Remove-Item -Force
Write-Host "  🧹 이전 상태 스냅샷을 정리했습니다." -ForegroundColor Gray

foreach ($S in $Strategies) {
    $command = "python main.py"
    
    # 환경변수 세팅과 함께 새 터미널 창(start)에서 실행
    $env_args = "`$env:STRATEGY_NAME='$($S.Name)'; `$env:MIN_EDGE=$($S.Edge); `$env:KELLY_FRACTION=$($S.Kelly); `$env:VOL_SCALE_FACTOR=$($S.Vol); `$env:ALPHA_BOOST_WEIGHT=$($S.Alpha); $command"
    
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "$env_args" -WindowStyle Minimized

    
    Write-Host "  ✅ [$($S.Name)] 마스터 출격 완료!" -ForegroundColor Green
    Write-Host "  ⏳ 다음 정예요원 출격까지 60초 대기 중..." -ForegroundColor Gray
    Start-Sleep -Seconds 60 # 1분당 한 명씩 안전하게 입장 (최강의 안정성)
}

Write-Host "`n🔥 시장진입~" -ForegroundColor Yellow
