@echo off
rem 토스증권 체결내역 동기화 실행 + 로그 기록 (작업 스케줄러가 호출)
cd /d "%~dp0.."
echo. >> sync\last_run.log
echo ===== %date% %time% ===== >> sync\last_run.log
python sync\toss_sync.py >> sync\last_run.log 2>&1
