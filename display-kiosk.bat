@echo off
rem Membuka halaman Display layar penuh (mode kiosk) untuk monitor produksi.
rem Tekan Alt+F4 untuk menutup.
set URL=http://localhost:5000/display?kiosk=1

rem Tunggu sampai server siap (maks. ~60 detik)
for /l %%i in (1,1,30) do (
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing %URL% -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" && goto buka
  timeout /t 2 /nobreak >nul
)

:buka
set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe
if exist "%CHROME%" (
  start "" "%CHROME%" --kiosk --noerrdialogs --disable-session-crashed-bubble --user-data-dir="%LocalAppData%\PIS-Display" %URL%
) else (
  start "" msedge --kiosk %URL% --edge-kiosk-type=fullscreen --no-first-run
)
