@echo off
rem 配信サーバ登録.bat - status.json を別ポートで配る（E案）
rem 2026-08-21 作成
rem ※ chcp は入れない（CLAUDE.md Z-5）
rem ※ アプリ（8501）には一切 触りません。別プロセス・別ポート 8600 です。

set APP=%~dp0
if "%APP:~-1%"=="\" set APP=%APP:~0,-1%

echo.
echo ============================================================
echo   1. まず最新の状態を書き出します
echo ============================================================
python "%APP%\emit_status.py"

echo.
echo ============================================================
echo   2. ログオン時に自動で立ち上がるよう登録します
echo ============================================================
schtasks /Create /TN "AI_ichiro_kensho_serve" /TR "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%APP%\serve_status.ps1\"" /SC ONLOGON /F

echo.
echo ============================================================
echo   3. いますぐ起動します（バックグラウンド）
echo ============================================================
schtasks /Run /TN "AI_ichiro_kensho_serve"

echo.
echo   5秒 待ってから、自分で自分に繋げるか試します
echo ============================================================
ping -n 6 127.0.0.1 > nul
curl -s -m 5 http://127.0.0.1:8600/status.json
echo.
echo.
echo ============================================================
echo   終わりました。この画面の内容をコピーして AI一郎 に貼ってください。
echo   ★AI一郎が個人PCから http://10.7.45.140:8600/ に繋がるかを確かめます。
echo ============================================================
echo.
pause