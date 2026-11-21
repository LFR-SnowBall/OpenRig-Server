<#
.SYNOPSIS
🏢 OpenRig-Server CLI for Windows PowerShell
Author: Luis Fernando Real (LFR-SnowBall) <luisitoreal01@gmail.com>
#>

# Prevent Git Bash / MSYS from converting Unix paths into Windows paths
$env:MSYS_NO_PATHCONV = "1"
$env:MSYS2_ARG_CONV_EXCL = "*"

$PSScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$winScript = Join-Path $PSScriptDir "rig-server"
$BridgeScript = Join-Path $PSScriptDir "scripts\ide-bridge.ps1"
$BridgePidFile = Join-Path $PSScriptDir "sockets\ide-bridge.pid"
$BridgeLogFile = Join-Path $PSScriptDir "logs\ide-bridge.log"
$WorkspacesDir = Join-Path $PSScriptDir "workspaces"

function Test-ServerIdeBridgeHealthy {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:7071/health" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
        return ($r.StatusCode -eq 200 -and $r.Content.Trim() -eq "OK")
    } catch {
        return $false
    }
}

function Start-ServerIdeBridge([switch]$Quiet) {
    if (Test-ServerIdeBridgeHealthy) {
        if (-not $Quiet) { Write-Host "✔ OpenRig Server Host IDE Bridge already active on 127.0.0.1:7071." -ForegroundColor Green }
        return
    }
    if (-not (Test-Path $BridgeScript)) { return }
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
        "-File", $BridgeScript, "-Mode", "server", "-WatchRoot", $WorkspacesDir,
        "-PidFile", $BridgePidFile, "-LogFile", $BridgeLogFile
    ) -WindowStyle Hidden | Out-Null
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Milliseconds 150
        if (Test-ServerIdeBridgeHealthy) { break }
    }
    if (-not $Quiet) {
        if (Test-ServerIdeBridgeHealthy) {
            Write-Host "🚀 OpenRig Server Host IDE Bridge started on 127.0.0.1:7071." -ForegroundColor Green
        } else {
            Write-Host "⚠️ OpenRig Server Host IDE Bridge did not confirm on 127.0.0.1:7071." -ForegroundColor Yellow
        }
    }
}

function Stop-ServerIdeBridge {
    if (Test-Path $BridgePidFile) {
        $bpid = (Get-Content $BridgePidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($bpid -and ($bpid -match '^\d+$')) {
            Stop-Process -Id ([int]$bpid) -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -Path $BridgePidFile -Force -ErrorAction SilentlyContinue
    }
    Write-Host "🛑 OpenRig Server Host IDE Bridge stopped." -ForegroundColor Yellow
}

function Resolve-CanonicalIde([string]$Name) {
    if ($null -eq $Name) { $Name = "" }
    $n = $Name.Trim().ToLowerInvariant()
    $aliases = @{
        ""                = "auto"
        "rig-open-ide"    = "auto"
        "default"         = "auto"
        "auto"            = "auto"
        "antigravity-ide" = "antigravity"
        "vscode"          = "code"
        "vscodium"        = "codium"
        "intellij"        = "idea"
    }
    if ($aliases.ContainsKey($n)) { $n = $aliases[$n] }
    $allowed = @("auto", "antigravity", "cursor", "code", "code-insiders", "windsurf", "kiro", "codium", "trae", "zed", "idea", "pycharm", "webstorm", "goland", "rider", "clion", "phpstorm", "rubymine", "rustrover", "gateway")
    if ($allowed -contains $n) { return $n }
    return $null
}

function Set-ServerDefaultIde([string]$IdeName) {
    $openrigHome = Join-Path $HOME ".openrig"
    $prefFile = Join-Path $openrigHome "ide"
    if ([string]::IsNullOrWhiteSpace($IdeName)) {
        $curr = if (-not [string]::IsNullOrWhiteSpace($env:OPENRIG_IDE)) {
            $env:OPENRIG_IDE
        } elseif (Test-Path $prefFile) {
            (Get-Content -Path $prefFile -TotalCount 1 -ErrorAction SilentlyContinue)
        } else {
            "auto"
        }
        Write-Host "Default Host IDE: $curr" -ForegroundColor Cyan
        Write-Host "Supported: auto, antigravity, cursor, code, code-insiders, windsurf, kiro, codium, trae, zed, idea, pycharm, webstorm, goland, rider, clion, phpstorm, rubymine, rustrover, gateway" -ForegroundColor Gray
        return
    }
    $norm = Resolve-CanonicalIde $IdeName
    if (-not $norm) {
        Write-Host "❌ Invalid IDE '$IdeName'." -ForegroundColor Red
        Write-Host "Supported: auto, antigravity, cursor, code, code-insiders, windsurf, kiro, codium, trae, zed, idea, pycharm, webstorm, goland, rider, clion, phpstorm, rubymine, rustrover, gateway" -ForegroundColor Yellow
        return
    }
    if (-not (Test-Path $openrigHome)) { New-Item -ItemType Directory -Path $openrigHome -Force | Out-Null }
    [System.IO.File]::WriteAllText($prefFile, "$norm`n", [System.Text.UTF8Encoding]::new($false))
    Write-Host "✅ Default Host IDE set to '$norm' ($prefFile)." -ForegroundColor Green
}

function Get-ServerBridgeIdesInfo {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:7071/ides" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
        if ($r.StatusCode -eq 200) { return ($r.Content -split "`r?`n") }
    } catch {}
    if (Test-Path $BridgeScript) {
        return @(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $BridgeScript -ListIdes)
    }
    return @()
}

function Show-ServerIdeList {
    $lines = Get-ServerBridgeIdesInfo
    if ($lines.Count -eq 0) {
        Write-Host "⚠️ Could not inspect Host IDEs." -ForegroundColor Yellow
        return
    }
    foreach ($ln in $lines) {
        if ($ln.Trim()) { Write-Host "  $ln" -ForegroundColor Cyan }
    }
}

function Show-ServerIdeBridgeStatus {
    $prefFile = Join-Path $HOME ".openrig\ide"
    $def = if (-not [string]::IsNullOrWhiteSpace($env:OPENRIG_IDE)) { $env:OPENRIG_IDE } elseif (Test-Path $prefFile) { ([string](Get-Content -Path $prefFile -TotalCount 1 -ErrorAction SilentlyContinue)).Trim() } else { "auto" }
    if (Test-ServerIdeBridgeHealthy) {
        $bpid = if (Test-Path $BridgePidFile) { (Get-Content $BridgePidFile -ErrorAction SilentlyContinue | Select-Object -First 1) } else { "unknown" }
        Write-Host "✅ OpenRig Server Host IDE Bridge: ONLINE (127.0.0.1:7071, PID=$bpid, default=$def)" -ForegroundColor Green
        Show-ServerIdeList
    } else {
        Write-Host "⏸️ OpenRig Server Host IDE Bridge: OFFLINE (default=$def)" -ForegroundColor Yellow
    }
}

if ($args.Count -gt 0 -and ($args[0] -in @("ide-bridge", "bridge"))) {
    $sub = if ($args.Count -gt 1) { $args[1].ToLower() } else { "status" }
    switch ($sub) {
        "start"   { Start-ServerIdeBridge }
        "stop"    { Stop-ServerIdeBridge }
        "restart" { Stop-ServerIdeBridge; Start-Sleep -Milliseconds 300; Start-ServerIdeBridge }
        "default" { $val = if ($args.Count -gt 2) { $args[2] } else { "" }; Set-ServerDefaultIde $val }
        "list"    { Show-ServerIdeList }
        default   { Show-ServerIdeBridgeStatus }
    }
    exit 0
}

$autoBridgeCmds = @("enter", "shell", "attach", "watch", "start", "restart", "recreate", "reload", "create", "new")
if ($args.Count -gt 0 -and ($args[0].ToLower() -in $autoBridgeCmds -or ($args[0].ToLower() -eq "user" -and $args.Count -gt 1 -and $args[1].ToLower() -in $autoBridgeCmds))) {
    Start-ServerIdeBridge -Quiet
}

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
        & wsl.exe -d $ubuntu /bin/bash "$wslScript" @args
        exit $LASTEXITCODE
    }

    # Test if default distro has bash
    $defaultOk = $false
    try {
        & wsl.exe bash -c "true" 2>$null
        if ($LASTEXITCODE -eq 0) { $defaultOk = $true }
    } catch {}

    if ($defaultOk) {
        & wsl.exe /bin/bash "$wslScript" @args
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
        & $gb "$winScript" @args
        exit $LASTEXITCODE
    }
}

Write-Host "❌ WSL (with Ubuntu) or Git Bash is required to run OpenRig-Server on Windows." -ForegroundColor Red
exit 1
