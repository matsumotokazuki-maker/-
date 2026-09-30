@echo off
cd /d "%~dp0"
echo ============================================================
echo   verify_pkl_fingerprint  (pkl fingerprint + code fingerprint)
echo ============================================================
python verify_pkl_fingerprint.py
echo.
echo ------------------------------------------------------------
echo   Compare every line with the baseline in éËèá.md
echo ------------------------------------------------------------
pause
