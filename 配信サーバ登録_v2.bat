@echo off
rem 配信サーバ登録_v2.bat - 管理者権限なしで常駐させる（2026-08-21 修正版）
rem 【v1の失敗】schtasks /SC ONLOGON は管理者権限が要る → アクセスが拒否されました
rem 【v2】スタートアップフォルダに置く方式へ変更。管理者権限は不要。
rem ※ chcp は入れない（CLAUDE.md Z-5）。アプリ(8501)には触りません。

set APP=%~dp0
if "%APP:~-1%"=="\" set APP=%APP:~0,-1%
set SU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup

echo.
echo ============================================================
echo   1. 最新の状態を書き出します
echo ============================================================
python "%APP%\emit_status.py"

echo.
echo ============================================================
echo   2. スタートアップに登録します（管理者権限は不要）
echo ============================================================
echo   置き場: %SU%
copy /Y "%APP%\AI_ichiro_status配信_起動.bat" "%SU%\AI_ichiro_status配信_起動.bat"
if errorlevel 1 (echo   [NG] コピーできませんでした) else (echo   [OK] 登録しました)

echo.
echo ============================================================
echo   3. いますぐ起動します
echo ============================================================
start "" /b powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%APP%\serve_status.ps1"
echo   起動を指示しました。5秒 待ちます。
ping -n 6 127.0.0.1 > nul

echo.
echo ============================================================
echo   4. 自分で自分に繋げるか試します
echo ============================================================
curl -s -m 5 http://127.0.0.1:8600/status.json
echo.
echo.
echo ============================================================
echo   終わりました。この画面をコピーして AI一郎 に貼ってください。
echo ============================================================
echo.
pause
