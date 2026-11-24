@echo off
rem Backup database + folder uploads ke folder tujuan (NAS / flashdisk).
rem Pemakaian:  backup.bat "E:\Backup-Produksi"
rem Tanpa argumen, backup disimpan ke folder "backup" di sebelah aplikasi.
setlocal
cd /d "%~dp0"
set TUJUAN=%~1
if "%TUJUAN%"=="" set TUJUAN=%~dp0backup

for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set STAMP=%%t
set DEST=%TUJUAN%\%STAMP%
mkdir "%DEST%" 2>nul

rem Salin database dengan API backup SQLite (aman walau aplikasi sedang berjalan)
py -c "import sqlite3,sys; s=sqlite3.connect('produksi.db'); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close(); s.close()" "%DEST%\produksi.db"
if errorlevel 1 (echo Gagal menyalin database. & exit /b 1)

robocopy "uploads" "%DEST%\uploads" /E /NFL /NDL /NJH /NJS /NP >nul
if %errorlevel% geq 8 (echo Gagal menyalin folder uploads. & exit /b 1)

echo Backup selesai: %DEST%

rem Hapus backup buatan script ini (nama folder yyyy-MM-dd_HHmm) yang lebih lama dari 30 hari
powershell -NoProfile -Command "Get-ChildItem -LiteralPath '%TUJUAN%' -Directory | Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}_\d{4}$' -and (Test-Path (Join-Path $_.FullName 'produksi.db')) -and $_.CreationTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Recurse -Force"
endlocal
