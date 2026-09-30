@echo off
chcp 65001 > nul
cd /d C:\Users\00000184\inventory_app

echo.
echo ============================================================
echo   ZAIKO KANRI APP - Data Update
echo ============================================================
echo.
echo  This batch will:
echo    1. Read CSVs from input_raw folder
echo    2. Apply JAN normalization
echo    3. Output to input_processed folder
echo    4. Update session_data.pkl
echo.
echo ============================================================
echo.

python update_data.py

echo.
pause
