@echo off
rem ==============================================================================
rem 🏢 OpenRig-Server Windows Command Shim
rem Allows running 'rig-server' seamlessly from PowerShell, CMD, or Windows Terminal
rem Author: Luis Fernando Real (LFR-SnowBall) <luisitoreal01@gmail.com>
rem ==============================================================================
setlocal
where bash.exe >nul 2>&1
if %ERRORLEVEL% equ 0 (
    bash "%~dp0rig-server" %*
) else (
    wsl bash "%~dp0rig-server" %*
)
endlocal
