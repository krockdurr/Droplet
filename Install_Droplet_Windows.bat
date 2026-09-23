@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0assets\assimilation_guides\Windows_install.ps1"
if errorlevel 1 (
    echo.
    echo Something went wrong - see the message above or in the popup window.
    pause
)
