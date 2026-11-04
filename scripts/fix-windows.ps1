<#
.SYNOPSIS
🩺 OpenRig-Server Windows Auto-Repair & Diagnostic Tool
Fixes WSL defaults, CRLF line endings, path translations, and container shims.

Usage:
  irm https://raw.githubusercontent.com/LFR-SnowBall/OpenRig-Server/main/scripts/fix-windows.ps1 | iex
#>

$ErrorActionPreference = "Continue"

Write-Host "`n==========================================================================" -ForegroundColor Cyan
Write-Host "🩺 OpenRig-Server Windows Auto-Repair Utility" -ForegroundColor Green
Write-Host "==========================================================================" -ForegroundColor Cyan

# ------------------------------------------------------------------------------
# 1. FIX CRLF -> LF LINE ENDINGS & MSYS PATH CONVERSION
# ------------------------------------------------------------------------------
Write-Host "`n[1/4] 🧹 Normalizing Line Endings (CRLF -> LF) & Git Bash Settings..." -ForegroundColor White

[Environment]::SetEnvironmentVariable("MSYS_NO_PATHCONV", "1", "User")
[Environment]::SetEnvironmentVariable("MSYS2_ARG_CONV_EXCL", "*", "User")
$env:MSYS_NO_PATHCONV = "1"
$env:MSYS2_ARG_CONV_EXCL = "*"

function Repair-DirLineEndings([string]$dir) {
    if (-not (Test-Path $dir)) { return 0 }
    $count = 0
    Get-ChildItem -Path $dir -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
        $_.Extension -in ".sh", ".py", ".md", ".env", ".db" -or $_.Name -in "rig", "rig-server", "VERSION", ".gitattributes"
    } | ForEach-Object {
        try {
            $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
            if ($bytes -contains 13) {
                $text = [System.IO.File]::ReadAllText($_.FullName)
                [System.IO.File]::WriteAllText($_.FullName, ($text -replace "`r`n", "`n"))
                $count++
            }
        } catch {}
    }
    return $count
}

$fixedServer = Repair-DirLineEndings (Join-Path $HOME ".openrig-server")
$fixedCurrent = 0
if (Test-Path ".\rig-server") {
    $fixedCurrent = Repair-DirLineEndings (Get-Location).Path
}

Write-Host "✔ Cleaned CRLF line endings ($fixedServer files in .openrig-server)." -ForegroundColor Green

# ------------------------------------------------------------------------------
# 2. CHECK & REPAIR WSL DEFAULT DISTRO
# ------------------------------------------------------------------------------
Write-Host "`n[2/4] 🐧 Checking WSL & Default Linux Distribution..." -ForegroundColor White

if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    try {
        $distros = wsl.exe -l -q 2>$null | ForEach-Object { $_ -replace '\x00','' } | Where-Object { $_ -match '\S' }
        $hasUbuntu = $false
        foreach ($d in $distros) {
            if ($d -match 'Ubuntu') { $hasUbuntu = $true; break }
        }

        # Check if default distro has bash
        $defaultHasBash = $false
        try {
            wsl.exe bash -c "true" 2>$null
            if ($LASTEXITCODE -eq 0) { $defaultHasBash = $true }
        } catch {}

        if (-not $defaultHasBash) {
            if ($hasUbuntu) {
                Write-Host "🔄 Default WSL distribution lacks /bin/bash (e.g. Rancher Desktop Alpine). Setting Ubuntu as default..." -ForegroundColor Yellow
                wsl.exe --set-default Ubuntu
                Write-Host "✔ Ubuntu is now the default WSL distribution." -ForegroundColor Green
            } else {
                Write-Host "⚠️ No standard Linux distro with bash found. Installing Ubuntu in WSL..." -ForegroundColor Yellow
                Start-Process -FilePath "wsl.exe" -ArgumentList "--install -d Ubuntu --no-launch" -Wait -NoNewWindow -ErrorAction SilentlyContinue
                wsl.exe --set-default Ubuntu
                Write-Host "✔ Ubuntu installed and set as default WSL distribution." -ForegroundColor Green
            }
        } else {
            Write-Host "✔ WSL default distribution has a healthy /bin/bash environment." -ForegroundColor Green
        }
    } catch {
        Write-Host "ℹ️ WSL status check passed." -ForegroundColor Yellow
    }
} else {
    Write-Host "⚠️ WSL is not installed. Container engine will require Git Bash." -ForegroundColor Yellow
}

# ------------------------------------------------------------------------------
# 3. REPAIR WINDOWS SHIMS & PATH TRANSLATIONS
# ------------------------------------------------------------------------------
Write-Host "`n[3/4] 🔧 Refreshing Windows CLI Shims (rig-server)..." -ForegroundColor White

$serverDir = Join-Path $HOME ".openrig-server"
if (Test-Path $serverDir) {
    $ps1Content = @'
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
$env:MSYS_NO_PATHCONV = "1"
$env:MSYS2_ARG_CONV_EXCL = "*"
$PSScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$winScript = Join-Path $PSScriptDir "rig-server"

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

if (Get-Command wsl -ErrorAction SilentlyContinue) {
    $wslScript = Convert-ToWslPath $winScript
    $defaultOk = $false
    try {
        & wsl.exe bash -c "true" 2>$null
        if ($LASTEXITCODE -eq 0) { $defaultOk = $true }
    } catch {}

    if ($defaultOk) {
        & wsl.exe bash -c "sed -i 's/\r$//' '$wslScript' 2>/dev/null; exec bash '$wslScript' `"$@`"" -- $Command @ArgsRest
        exit $LASTEXITCODE
    }

    $distros = wsl.exe -l -q 2>$null | ForEach-Object { $_ -replace '\x00','' } | Where-Object { $_ -match '\S' }
    $ubuntu = $distros | Where-Object { $_ -match 'Ubuntu' } | Select-Object -First 1
    if ($ubuntu) {
        & wsl.exe -d $ubuntu bash -c "sed -i 's/\r$//' '$wslScript' 2>/dev/null; exec bash '$wslScript' `"$@`"" -- $Command @ArgsRest
        exit $LASTEXITCODE
    }
}
Write-Host "❌ Git Bash or WSL with Ubuntu is required to run OpenRig-Server." -ForegroundColor Red
exit 1
'@
    [System.IO.File]::WriteAllText((Join-Path $serverDir "rig-server.ps1"), ($ps1Content -replace "`r`n", "`n"))

    $cmdContent = "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"%~dp0rig-server.ps1`" %*`r`n"
    [System.IO.File]::WriteAllText((Join-Path $serverDir "rig-server.cmd"), $cmdContent)
    [System.IO.File]::WriteAllText((Join-Path $serverDir "rig-server.bat"), "@echo off`r`ncall `"%~dp0rig-server.cmd`" %*`r`n")
    Write-Host "✔ OpenRig-Server shims repaired and path-mapped in $serverDir." -ForegroundColor Green
}

# ------------------------------------------------------------------------------
# 4. CONTAINER ENGINE VERIFICATION
# ------------------------------------------------------------------------------
Write-Host "`n[4/4] 🐳 Verifying Container Engine (Rancher / Docker)..." -ForegroundColor White

$dockerRunning = $false
try {
    $dockerVer = (docker version --format '{{.Server.Version}}' 2>$null)
    if ($dockerVer) {
        $dockerRunning = $true
        Write-Host "✔ Container engine is running ($dockerVer)." -ForegroundColor Green
    }
} catch {}

if (-not $dockerRunning) {
    Write-Host "⚠️ Container engine is not responding yet." -ForegroundColor Yellow
    Write-Host "   Make sure Rancher Desktop or Docker Desktop is opened and running." -ForegroundColor Yellow
}

Write-Host "`n==========================================================================" -ForegroundColor Cyan
Write-Host "🎉 REPAIR COMPLETE! OpenRig-Server is ready for Windows." -ForegroundColor Green
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "👉 You can now run in PowerShell:" -ForegroundColor White
Write-Host "   rig-server user create luis --ram 16g --cpus 6 --role fullstack" -ForegroundColor Cyan
Write-Host ""
