<#
.SYNOPSIS
🏢 OpenRig-Server CLI for Windows PowerShell
Author: Luis Fernando Real (LFR-SnowBall) <luisitoreal01@gmail.com>
#>

param(
    [Parameter(Position=0)]
    [string]$Command = "help",
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ArgsRest
)

# Prevent Git Bash from converting Unix paths (/dev/net/tun) into Windows paths (C:\Program Files\Git\...)
$env:MSYS_NO_PATHCONV = "1"
$env:MSYS2_ARG_CONV_EXCL = "*"

$PSScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$winScript = Join-Path $PSScriptDir "rig-server"

# Function to convert Windows path to WSL /mnt/... path
function Convert-ToWslPath([string]$winPath) {
    try {
        $raw = (wsl.exe wslpath -a -u "$winPath" 2>$null)
        if ($raw) {
            $cleaned = ($raw -replace '\x00','').Trim()
            if ($cleaned -match '^/mnt/') { return $cleaned }
        }
    } catch {}
    if ($winPath -match '^([A-Za-z]):[\\/](.*)') {
        $drive = $matches[1].ToLower()
        $rest = $matches[2] -replace '\\','/'
        return "/mnt/$drive/$rest"
    }
    return $winPath -replace '\\','/'
}

# 1. Prefer Git Bash if available on Windows host
$gitBashCandidates = @(
    "$env:ProgramFiles\Git\bin\bash.exe",
    "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
    "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
)

foreach ($gb in $gitBashCandidates) {
    if (Test-Path $gb) {
        & $gb "$winScript" $Command @ArgsRest
        exit $LASTEXITCODE
    }
}

# 2. Check WSL
if (Get-Command wsl -ErrorAction SilentlyContinue) {
    $wslScript = Convert-ToWslPath $winScript

    # Test if default distro has bash
    $defaultOk = $false
    try {
        & wsl.exe bash -c "true" 2>$null
        if ($LASTEXITCODE -eq 0) { $defaultOk = $true }
    } catch {}

    if ($defaultOk) {
        # Normalize CRLF in case Git on Windows cloned with CRLF, then execute
        & wsl.exe bash -c "sed -i 's/\r$//' '$wslScript' 2>/dev/null; exec bash '$wslScript' `"$@`"" -- $Command @ArgsRest
        exit $LASTEXITCODE
    }

    # If default failed (e.g. rancher-desktop Alpine without bash), look for Ubuntu
    $distros = wsl.exe -l -q 2>$null | ForEach-Object { $_ -replace '\x00','' } | Where-Object { $_ -match '\S' }
    $ubuntu = $distros | Where-Object { $_ -match 'Ubuntu' } | Select-Object -First 1
    if ($ubuntu) {
        & wsl.exe -d $ubuntu bash -c "sed -i 's/\r$//' '$wslScript' 2>/dev/null; exec bash '$wslScript' `"$@`"" -- $Command @ArgsRest
        exit $LASTEXITCODE
    }

    Write-Host "❌ Error: WSL does not have a standard Linux distro with bash installed." -ForegroundColor Red
    Write-Host "💡 Rancher Desktop's internal Alpine distro does not include /bin/bash." -ForegroundColor Yellow
    Write-Host "   Run this in PowerShell to install and set Ubuntu as default:" -ForegroundColor Yellow
    Write-Host "     wsl --install -d Ubuntu" -ForegroundColor Cyan
    Write-Host "     wsl --set-default Ubuntu" -ForegroundColor Cyan
    exit 1
}

Write-Host "❌ Git Bash or WSL is required to run OpenRig-Server on Windows." -ForegroundColor Red
exit 1
