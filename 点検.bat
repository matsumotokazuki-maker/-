@echo off
cd /d "%~dp0"
echo === 取込ルーティン 連続性チェック（検証PC） ===
echo.
python sync_check.py --days 35
if errorlevel 9009 echo [!] python が見つかりません。py -3 sync_check.py --days 35 をお試しください。
echo.
echo 終わったら data\health_rows.csv を個人PCへコピーしてください。
pause
