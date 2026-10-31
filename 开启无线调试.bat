@echo off
rem Enable TCP/IP wireless debugging on a HarmonyOS 4.x device over USB.
rem Usage: plug the device in with USB debugging allowed, then double-click this file.
rem After "Done", unplug the cable and add  DEVICE_IP:5555  in the app settings.
setlocal enabledelayedexpansion
set "ADB=%~dp0tools\adb\adb.exe"

if not exist "%ADB%" (
    echo [ERROR] tools\adb\adb.exe not found next to this script.
    pause
    exit /b 1
)

echo ============================================
echo    Harmony Wireless Debug Enabler
echo ============================================
echo.
echo [1/3] Scanning USB devices...
"%ADB%" devices

set "FOUND="
for /f "skip=1 tokens=1,2" %%a in ('"%ADB%" devices') do (
    if "%%b"=="device" (
        rem Skip targets that are already connected over TCP (they contain a colon).
        echo %%a | find ":" >nul
        if errorlevel 1 (
            echo.
            echo [2/3] Enabling TCP mode for %%a ...
            "%ADB%" -s %%a tcpip 5555
            set "FOUND=1"
        )
    )
)

if not defined FOUND (
    echo.
    echo [!] No USB device detected.
    echo     Plug in the device with USB debugging enabled, then retry.
    pause
    exit /b 1
)

echo.
echo [3/3] Waiting for adbd to restart...
timeout /t 6 /nobreak >nul
echo.
echo ============================================
echo   Done. Unplug the USB cable now.
echo   The monitor will reconnect automatically.
echo ============================================
pause
