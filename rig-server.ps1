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

$PSScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = (Join-Path $PSScriptDir "rig-server") -replace '\\','/'

if (Get-Command bash -ErrorAction SilentlyContinue) {
    & bash $scriptPath $Command @ArgsRest
} elseif (Get-Command wsl -ErrorAction SilentlyContinue) {
    & wsl bash $scriptPath $Command @ArgsRest
} else {
    Write-Host "❌ Bash or WSL is required to run OpenRig-Server on Windows." -ForegroundColor Red
}
