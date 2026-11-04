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

# Prevent Git Bash / MSYS from converting Unix paths into Windows paths
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

# 1. Prefer WSL with Ubuntu (Provides real Linux environment with python3, lsof, background daemons)
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    $wslScript = Convert-ToWslPath $winScript
    $distros = wsl.exe -l -q 2>$null | ForEach-Object { $_ -replace '\x00','' } | Where-Object { $_ -match '\S' }
    $ubuntu = $distros | Where-Object { $_ -match 'Ubuntu' } | Select-Object -First 1

    if ($ubuntu) {
        & wsl.exe -d $ubuntu bash -c "sed -i 's/\r$//' '$wslScript' 2>/dev/null; exec bash '$wslScript' `"$@`"" -- $Command @ArgsRest
        exit $LASTEXITCODE
    }

    # Test if default distro has bash
    $defaultOk = $false
    try {
        & wsl.exe bash -c "true" 2>$null
        if ($LASTEXITCODE -eq 0) { $defaultOk = $true }
    } catch {}

    if ($defaultOk) {
        & wsl.exe bash -c "sed -i 's/\r$//' '$wslScript' 2>/dev/null; exec bash '$wslScript' `"$@`"" -- $Command @ArgsRest
        exit $LASTEXITCODE
    }
}

# 2. Fallback to Git Bash if WSL is not available
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

Write-Host "❌ WSL (with Ubuntu) or Git Bash is required to run OpenRig-Server on Windows." -ForegroundColor Red
exit 1
