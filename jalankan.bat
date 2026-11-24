@echo off
rem Menjalankan Sistem Produksi Buku di http://localhost:5000
rem Pasang di Task Scheduler dengan trigger "At log on" agar jalan otomatis.
cd /d "%~dp0"
title Sistem Produksi Buku
py app.py
if errorlevel 1 (
  echo.
  echo Gagal menjalankan. Pastikan Python terpasang dan sudah menjalankan:
  echo     py -m pip install -r requirements.txt
  pause
)
