@echo off
cd /d "%~dp0"
echo ============================================================
echo   YOYU-NISSU SHINDAN  (read only / no data change)
echo ============================================================
echo.

set PY=
where python >nul 2>&1 && set PY=python
if "%PY%"=="" (where py >nul 2>&1 && set PY=py)
if "%PY%"=="" (if exist "python\python.exe" set PY=python\python.exe)
if "%PY%"=="" (if exist "..\python\python.exe" set PY=..\python\python.exe)
if "%PY%"=="" (if exist "venv\Scripts\python.exe" set PY=venv\Scripts\python.exe)
if "%PY%"=="" (if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe)

if "%PY%"=="" (
  echo [NG] python not found.
  echo      Open update_data.bat with Notepad and check how python is called.
  echo.
  pause
  exit /b 1
)

echo python = %PY%
echo.
%PY% "zaiko_shindan.py" > "shindan_result.txt" 2>&1

echo ------------------------------------------------------------
echo   Result written to: shindan_result.txt
echo ------------------------------------------------------------
echo.
chcp 65001 >nul
type "shindan_result.txt"
echo.
pause
