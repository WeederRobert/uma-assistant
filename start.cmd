@echo off
chcp 65001 >nul
setlocal
title Uma Assistant

set "LAUNCHER=%~dp0scripts\launch.ps1"
if not exist "%LAUNCHER%" (
    echo [ERROR] Missing %LAUNCHER%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo [ERROR] Launcher exited with code %CODE%
    pause
)

endlocal & exit /b %CODE%
