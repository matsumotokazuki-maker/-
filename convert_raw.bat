@echo off
chcp 65001 > nul
cd /d C:\Users\00000184\inventory_app

echo.
echo ============================================================
echo   ZAIKO KANRI APP - Convert Raw Data
echo ============================================================
echo.
echo  This batch will:
echo    1. Read raw files from business_system_export\ folder
echo    2. Convert to update_data.py format
echo    3. Output to input_raw\ folder
echo.
echo ============================================================
echo.

python convert_raw_to_app.py

echo.
pause
