@echo off
chcp 65001 > nul
echo ============================================
echo  WhaleCopy Bot - 이력 초기화
echo ============================================
echo.
echo 다음 파일들이 삭제됩니다:
echo   - status_WhaleCopy.json  (잔고 / 포지션 상태)
echo   - trade_history.jsonl    (거래 이력)
echo   - bot_live.log           (로그)
echo.
set /p CONFIRM="정말 삭제하겠습니까? (y/N): "
if /i not "%CONFIRM%"=="y" (
    echo 취소되었습니다.
    pause
    exit /b
)

echo.
if exist "status_WhaleCopy.json" (
    del "status_WhaleCopy.json"
    echo [OK] status_WhaleCopy.json 삭제 완료
) else (
    echo [--] status_WhaleCopy.json 없음 (스킵)
)

if exist "trade_history.jsonl" (
    del "trade_history.jsonl"
    echo [OK] trade_history.jsonl 삭제 완료
) else (
    echo [--] trade_history.jsonl 없음 (스킵)
)

if exist "bot_live.log" (
    del "bot_live.log"
    echo [OK] bot_live.log 삭제 완료
) else (
    echo [--] bot_live.log 없음 (스킵)
)

echo.
echo 이력 초기화 완료. 봇을 새로 시작하면 됩니다.
echo (whales.json 은 보존됨 - 고래 평가 데이터)
echo.
pause
