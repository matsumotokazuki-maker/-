@echo off
chcp 65001 > nul
cd /d C:\Users\00000184\inventory_app

echo.
echo ============================================================
echo   ZAIKO KANRI APP - Starting...
echo ============================================================
echo.
echo  IP Address List:
ipconfig | findstr "IPv4"
echo.
echo  Tablet URL: http://[IP-Address]:8501
echo  (use the IP for your Wi-Fi network)
echo.
echo ============================================================
echo.

streamlit run inventory_app.py --server.address=0.0.0.0 --server.port=8501
pause