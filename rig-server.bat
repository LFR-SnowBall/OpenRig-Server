@echo off
rem ==============================================================================
rem 🏢 OpenRig-Server Windows Command Shim (.bat wrapper)
rem ==============================================================================
setlocal
where bash.exe >nul 2>&1
if %ERRORLEVEL% equ 0 (
    bash "%~dp0rig-server" %*
) else (
    wsl bash "%~dp0rig-server" %*
)
endlocal
