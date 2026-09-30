@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================================
echo   余裕日数 診断 を実行します（読み取りのみ）
echo ============================================================
echo.

set PY=
where python > nul 2>&1 && set PY=python
if "%PY%"=="" (where py > nul 2>&1 && set PY=py)
if "%PY%"=="" (if exist "python\python.exe" set PY=python\python.exe)
if "%PY%"=="" (if exist "..\python\python.exe" set PY=..\python\python.exe)

if "%PY%"=="" (
  echo [NG] python が見つかりませんでした。
  echo      update_data.bat をメモ帳で開いて、python の呼び方を確認してください。
  echo.
  pause
  exit /b 1
)

echo 使う python : %PY%
echo.
%PY% "余裕日数_診断_20260827.py" > "診断結果.txt" 2>&1

echo.
echo ------------------------------------------------------------
echo   結果を 診断結果.txt に書きました。
echo   同じフォルダに出来ています。中身をコピーして貼ってください。
echo ------------------------------------------------------------
echo.
type "診断結果.txt"
echo.
pause
