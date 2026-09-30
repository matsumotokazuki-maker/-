@echo off
rem 登録.bat - タスクスケジューラに「状態の書き出し」を登録する
rem 2026-08-21 作成（人的介在W1 実装3）
rem ※ chcp は入れない（検証PCのコンソールは cp932・CLAUDE.md Z-5）
rem ※ 管理者権限は不要（既存2本も RunLevel=Limited で動いている）

set APP=%~dp0
if "%APP:~-1%"=="\" set APP=%APP:~0,-1%

echo.
echo ============================================================
echo   1. まず 1回 手で回して、動くかを見ます
echo ============================================================
python "%APP%\emit_status.py"

echo.
echo ============================================================
echo   2. タスクスケジューラに登録します（毎日 17:30）
echo ============================================================
schtasks /Create /TN "AI_ichiro_kensho_status" /TR "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"%APP%\emit_status.ps1\"" /SC DAILY /ST 17:30 /F

echo.
echo ============================================================
echo   3. 登録できたかを確認します
echo ============================================================
schtasks /Query /TN "AI_ichiro_kensho_status" /V /FO LIST | findstr /C:"タスク名" /C:"次回" /C:"実行するタスク" /C:"TaskName" /C:"Next Run" /C:"Task To Run"

echo.
echo 終わりました。この画面の内容をコピーして AI一郎 に貼ってください。
echo.
pause