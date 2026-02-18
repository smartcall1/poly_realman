
$Strategies = @(
    @{ Name = "ConsrvGiant"; Edge = 0.10; Kelly = 0.10; Vol = 1.6; Alpha = 0.10 },
    @{ Name = "TrendFollow"; Edge = 0.04; Kelly = 0.25; Vol = 1.2; Alpha = 0.45 },
    @{ Name = "MeanRevert"; Edge = 0.05; Kelly = 0.20; Vol = 1.3; Alpha = -0.20 },
    @{ Name = "GammaScalp"; Edge = 0.03; Kelly = 0.30; Vol = 1.1; Alpha = 0.25 },
    @{ Name = "TailHunter"; Edge = 0.12; Kelly = 0.15; Vol = 1.8; Alpha = 0.30 },
    @{ Name = "KellyPurist"; Edge = 0.02; Kelly = 0.50; Vol = 1.2; Alpha = 0.05 },
    @{ Name = "SmartMoney"; Edge = 0.06; Kelly = 0.20; Vol = 1.3; Alpha = 0.25 },
    @{ Name = "HF_Sniper"; Edge = 0.01; Kelly = 0.15; Vol = 1.0; Alpha = 0.20 },
    @{ Name = "RiskOnSpec"; Edge = 0.04; Kelly = 0.40; Vol = 1.0; Alpha = 0.35 },
    @{ Name = "AdaptiveMst"; Edge = 0.05; Kelly = 0.25; Vol = 1.25; Alpha = 0.25 },
    @{ Name = "FiveMinMom"; Edge = 0.08; Kelly = 0.12; Vol = 1.0; Alpha = 0.28 },
    @{ Name = "RegimeBrake"; Edge = 0.12; Kelly = 0.08; Vol = 1.4; Alpha = 0.00 }
)

Write-Host "🚀 트레이딩 군단출격" -ForegroundColor Cyan

# [NEW] 거래 내역 리셋 (기존 로그 백업)
$HistoryFile = "trade_history.jsonl"
if (Test-Path $HistoryFile) {
    $Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $BackupFile = "trade_history_backup_$Timestamp.jsonl"
    Move-Item -Path $HistoryFile -Destination $BackupFile
    Write-Host "  📦 기존 거래 내역을 백업했습니다: $BackupFile" -ForegroundColor Gray
}

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
