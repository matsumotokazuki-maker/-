@echo off
rem AI_ichiro_status配信_起動.bat
rem 2026-08-21 作成。ログオン時に status 配信サーバを立ち上げる。
rem ★管理者権限は要りません（スタートアップフォルダ方式）。
rem ★アプリ（8501）には一切 触りません。別プロセス・別ポート 8600 です。
start "" /b powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\kashidashi01\Desktop\inventory_app_dist\serve_status.ps1"
