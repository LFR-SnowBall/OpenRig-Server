<#
.SYNOPSIS
OpenRig Native Windows Host IDE Bridge & Trigger Watcher (Zero-Admin, 127.0.0.1 Only)
Supports both OpenRig-Server ('server' mode) and OpenRig Client ('client' mode),
and routes across Antigravity, Cursor, VS Code (+ Insiders), Windsurf, Kiro, VSCodium, Trae, Zed, and JetBrains Gateway.
#>
param(
    [ValidateSet('server', 'client')]
    [string]$Mode = 'server',
    [string]$WatchRoot = '',
    [int]$Port = 7071,
    [string]$PidFile = '',
    [string]$LogFile = '',
    [switch]$ListIdes,
    [switch]$OpenOnce,
    [string]$Alias = '',
    [string]$Path = '/workspace',
    [string]$Ide = 'auto'
)

$ErrorActionPreference = 'Continue'
$env:ELECTRON_RUN_AS_NODE = $null
$env:VSCODE_DEV = $null

$script:VsCodeIdeOrder = @('antigravity', 'cursor', 'code', 'code-insiders', 'windsurf', 'kiro', 'codium', 'trae')
$script:JetBrainsCodes = [ordered]@{
    'idea'      = 'IU'
    'pycharm'   = 'PY'
    'webstorm'  = 'WS'
    'goland'    = 'GO'
    'rider'     = 'RD'
    'clion'     = 'CL'
    'phpstorm'  = 'PS'
    'rubymine'  = 'RM'
    'rustrover' = 'RR'
    'gateway'   = ''
}
$script:IdeAliases = @{
    ''                = 'auto'
    'rig-open-ide'    = 'auto'
    'default'         = 'auto'
    'auto'            = 'auto'
    'antigravity-ide' = 'antigravity'
    'vscode'          = 'code'
    'vscodium'        = 'codium'
    'intellij'        = 'idea'
}
$script:AllowedIdes = @('auto') + $script:VsCodeIdeOrder + @('zed') + @($script:JetBrainsCodes.Keys)

function Write-BridgeLog([string]$Message) {
    if (-not $LogFile) { return }
    $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    try {
        $d = Split-Path -Parent $LogFile
        if ($d -and -not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
        Add-Content -Path $LogFile -Value "[$ts] $Message" -Encoding UTF8
    } catch {}
}

function Normalize-IdeName([string]$Name) {
    if ($null -eq $Name) { $Name = '' }
    $n = $Name.Trim().ToLowerInvariant()
    if ($script:IdeAliases.ContainsKey($n)) { return $script:IdeAliases[$n] }
    return $n
}

function Test-SafeIde([string]$Name) {
    $n = Normalize-IdeName $Name
    return ($script:AllowedIdes -contains $n)
}

function Test-SafeAlias([string]$Alias) {
    return ($Alias -cmatch '^[a-zA-Z0-9._-]{1,64}$')
}

function Test-SafePath([string]$TargetPath) {
    if ([string]::IsNullOrWhiteSpace($TargetPath)) { return $false }
    if ($TargetPath -match '\.\.') { return $false }
    return ($TargetPath -cmatch '^/[a-zA-Z0-9._/@+\- ]{0,512}$')
}

function Get-DefaultHostIde {
    if (-not [string]::IsNullOrWhiteSpace($env:OPENRIG_IDE)) {
        $n = Normalize-IdeName $env:OPENRIG_IDE
        if ($script:AllowedIdes -contains $n) { return $n }
    }
    $prefFile = Join-Path $HOME '.openrig\ide'
    if (Test-Path $prefFile) {
        try {
            $first = Get-Content -Path $prefFile -TotalCount 1 -ErrorAction SilentlyContinue
            if ($first) {
                $n = Normalize-IdeName ([string]$first)
                if ($script:AllowedIdes -contains $n) { return $n }
            }
        } catch {}
    }
    return 'auto'
}

function Find-IdeBinary([string]$IdeId) {
    $local = $env:LOCALAPPDATA
    $prog = $env:ProgramFiles
    $specs = @{
        'antigravity'   = @{
            Paths = @(
                (Join-Path $local 'Programs\Antigravity IDE\Antigravity IDE.exe'),
                (Join-Path $local 'Programs\Antigravity IDE\bin\antigravity-ide.cmd')
            )
            Cmds = @('antigravity-ide')
        }
        'cursor'        = @{
            Paths = @(
                (Join-Path $local 'Programs\cursor\Cursor.exe'),
                (Join-Path $local 'Programs\cursor\resources\app\bin\cursor.cmd')
            )
            Cmds = @('cursor')
        }
        'code'          = @{
            Paths = @(
                (Join-Path $local 'Programs\Microsoft VS Code\Code.exe'),
                (Join-Path $prog  'Microsoft VS Code\Code.exe'),
                (Join-Path $local 'Programs\Microsoft VS Code\bin\code.cmd'),
                (Join-Path $prog  'Microsoft VS Code\bin\code.cmd')
            )
            Cmds = @('code')
        }
        'code-insiders' = @{
            Paths = @(
                (Join-Path $local 'Programs\Microsoft VS Code Insiders\Code - Insiders.exe'),
                (Join-Path $prog  'Microsoft VS Code Insiders\Code - Insiders.exe'),
                (Join-Path $local 'Programs\Microsoft VS Code Insiders\bin\code-insiders.cmd')
            )
            Cmds = @('code-insiders')
        }
        'windsurf'      = @{
            Paths = @(
                (Join-Path $local 'Programs\Windsurf\Windsurf.exe'),
                (Join-Path $prog  'Windsurf\Windsurf.exe'),
                (Join-Path $local 'Programs\Windsurf\bin\windsurf.cmd')
            )
            Cmds = @('windsurf')
        }
        'kiro'          = @{
            Paths = @(
                (Join-Path $local 'Programs\Kiro\Kiro.exe'),
                (Join-Path $prog  'Kiro\Kiro.exe'),
                (Join-Path $local 'Programs\Kiro\bin\kiro.cmd')
            )
            Cmds = @('kiro')
        }
        'codium'        = @{
            Paths = @(
                (Join-Path $local 'Programs\VSCodium\VSCodium.exe'),
                (Join-Path $prog  'VSCodium\VSCodium.exe'),
                (Join-Path $local 'Programs\VSCodium\bin\codium.cmd')
            )
            Cmds = @('codium')
        }
        'trae'          = @{
            Paths = @(
                (Join-Path $local 'Programs\Trae\Trae.exe'),
                (Join-Path $prog  'Trae\Trae.exe'),
                (Join-Path $local 'Programs\Trae\bin\trae.cmd')
            )
            Cmds = @('trae')
        }
        'zed'           = @{
            Paths = @(
                (Join-Path $local 'Programs\Zed\Zed.exe'),
                (Join-Path $local 'Zed\Zed.exe'),
                (Join-Path $prog  'Zed\Zed.exe')
            )
            Cmds = @('zed')
        }
    }
    if (-not $specs.ContainsKey($IdeId)) { return $null }
    $spec = $specs[$IdeId]
    foreach ($p in $spec.Paths) {
        if ($p -and (Test-Path $p -ErrorAction SilentlyContinue)) { return $p }
    }
    foreach ($cmd in $spec.Cmds) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found -and $found.Source -and (Test-Path $found.Source -ErrorAction SilentlyContinue) -and ($found.Source -notlike '*\Microsoft\WindowsApps\*')) {
            return $found.Source
        }
    }
    return $null
}

function Test-JetBrainsGatewayAvailable {
    foreach ($regPath in @(
        'Registry::HKEY_CLASSES_ROOT\jetbrains-gateway',
        'HKCU:\Software\Classes\jetbrains-gateway',
        'HKLM:\Software\Classes\jetbrains-gateway'
    )) {
        try {
            if (Test-Path $regPath -ErrorAction SilentlyContinue) { return $true }
        } catch {}
    }
    $jbPaths = @(
        (Join-Path $env:LOCALAPPDATA 'JetBrains\Toolbox\bin\jetbrains-toolbox.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\JetBrains Gateway\bin\gateway64.exe')
    )
    foreach ($p in $jbPaths) {
        if ($p -and (Test-Path $p -ErrorAction SilentlyContinue)) { return $true }
    }
    return $false
}

function Get-InstalledHostIdes {
    $installed = New-Object System.Collections.Generic.List[string]
    foreach ($id in $script:VsCodeIdeOrder) {
        if (Find-IdeBinary $id) { [void]$installed.Add($id) }
    }
    if (Find-IdeBinary 'zed') { [void]$installed.Add('zed') }
    if (Test-JetBrainsGatewayAvailable) {
        foreach ($jb in $script:JetBrainsCodes.Keys) {
            [void]$installed.Add($jb)
        }
    }
    return @($installed)
}

function Resolve-SshConfigTarget([string]$Alias) {
    $result = @{
        HostName = $Alias
        Port     = '22'
        User     = 'rig'
    }
    $cfg = Join-Path $HOME '.ssh\config'
    if (-not (Test-Path $cfg -ErrorAction SilentlyContinue)) { return $result }

    $inBlock = $false
    foreach ($rawLine in (Get-Content -Path $cfg -ErrorAction SilentlyContinue)) {
        $line = ($rawLine -split '#', 2)[0].Trim()
        if (-not $line) { continue }
        if ($line -match '^(?i)Host\s+(.+)$') {
            $patterns = ($matches[1].Trim() -split '\s+')
            $inBlock = ($patterns -contains $Alias)
            continue
        }
        if ($line -match '^(?i)Match\s+') {
            $inBlock = $false
            continue
        }
        if (-not $inBlock) { continue }
        if ($line -match '^(?i)HostName\s+(\S+)$') {
            $v = $matches[1].Trim()
            if ($v -cmatch '^[a-zA-Z0-9._:-]{1,128}$') { $result.HostName = $v }
        } elseif ($line -match '^(?i)Port\s+(\d{1,5})$') {
            $p = [int]$matches[1]
            if ($p -ge 1 -and $p -le 65535) { $result.Port = "$p" }
        } elseif ($line -match '^(?i)User\s+(\S+)$') {
            $u = $matches[1].Trim()
            if ($u -cmatch '^[a-zA-Z0-9._-]{1,64}$') { $result.User = $u }
        }
    }
    return $result
}

function Find-HostIde([string]$RequestedIde = 'auto') {
    $norm = Normalize-IdeName $RequestedIde
    if (-not ($script:AllowedIdes -contains $norm)) { return $null }

    if ($norm -eq 'auto') {
        $norm = Get-DefaultHostIde
    }

    if ($norm -eq 'auto') {
        foreach ($id in $script:VsCodeIdeOrder) {
            $bin = Find-IdeBinary $id
            if ($bin) {
                return @{ Id = $id; Protocol = 'vscode'; Binary = $bin; Code = '' }
            }
        }
        $zedBin = Find-IdeBinary 'zed'
        if ($zedBin) {
            return @{ Id = 'zed'; Protocol = 'zed'; Binary = $zedBin; Code = '' }
        }
        if (Test-JetBrainsGatewayAvailable) {
            return @{ Id = 'gateway'; Protocol = 'jetbrains'; Binary = 'jetbrains-gateway://'; Code = '' }
        }
        return $null
    }

    if ($script:VsCodeIdeOrder -contains $norm) {
        $bin = Find-IdeBinary $norm
        if ($bin) {
            return @{ Id = $norm; Protocol = 'vscode'; Binary = $bin; Code = '' }
        }
        $def = Get-DefaultHostIde
        if (($script:VsCodeIdeOrder -contains $def) -and ($def -ne $norm)) {
            $defBin = Find-IdeBinary $def
            if ($defBin) {
                return @{ Id = $def; Protocol = 'vscode'; Binary = $defBin; Code = '' }
            }
        }
        foreach ($id in $script:VsCodeIdeOrder) {
            $fallbackBin = Find-IdeBinary $id
            if ($fallbackBin) {
                return @{ Id = $id; Protocol = 'vscode'; Binary = $fallbackBin; Code = '' }
            }
        }
        return $null
    }

    if ($norm -eq 'zed') {
        $zedBin = Find-IdeBinary 'zed'
        if ($zedBin) {
            return @{ Id = 'zed'; Protocol = 'zed'; Binary = $zedBin; Code = '' }
        }
        return $null
    }

    if ($script:JetBrainsCodes.Contains($norm)) {
        if (Test-JetBrainsGatewayAvailable) {
            return @{ Id = $norm; Protocol = 'jetbrains'; Binary = 'jetbrains-gateway://'; Code = $script:JetBrainsCodes[$norm] }
        }
        return $null
    }

    return $null
}

function Start-NativeCommand([string]$FilePath, [string[]]$ArgumentList) {
    $env:ELECTRON_RUN_AS_NODE = $null
    $env:VSCODE_DEV = $null
    if ($FilePath -match '\.(cmd|bat)$') {
        Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WindowStyle Hidden | Out-Null
    } else {
        Start-Process -FilePath $FilePath -ArgumentList $ArgumentList | Out-Null
    }
}

function Open-HostIde([string]$Alias, [string]$TargetPath, [string]$RequestedIde = 'auto') {
    if (-not (Test-SafeAlias $Alias) -or -not (Test-SafePath $TargetPath) -or -not (Test-SafeIde $RequestedIde)) {
        Write-BridgeLog "[REJECTED] invalid alias='$Alias' path='$TargetPath' ide='$RequestedIde'"
        return $false
    }
    $reqNorm = Normalize-IdeName $RequestedIde
    $resolved = Find-HostIde $reqNorm
    if (-not $resolved) {
        Write-BridgeLog "[ERROR] No compatible IDE found (requested=$reqNorm)"
        return $false
    }

    $uriPath = $TargetPath -replace ' ', '%20'
    try {
        switch ($resolved.Protocol) {
            'vscode' {
                $uri = "vscode-remote://ssh-remote+$Alias$uriPath"
                Write-BridgeLog "[OPEN] requested=$reqNorm resolved=$($resolved.Id) alias=$Alias path=$TargetPath uri=$uri ide=$($resolved.Binary)"
                Start-NativeCommand $resolved.Binary @('--new-window', '--folder-uri', $uri)
                return $true
            }
            'zed' {
                $uri = "ssh://$Alias$uriPath"
                Write-BridgeLog "[OPEN] requested=$reqNorm resolved=zed alias=$Alias path=$TargetPath uri=$uri ide=$($resolved.Binary)"
                Start-NativeCommand $resolved.Binary @($uri)
                return $true
            }
            'jetbrains' {
                $ssh = Resolve-SshConfigTarget $Alias
                $encHost = [System.Uri]::EscapeDataString($ssh.HostName)
                $encUser = [System.Uri]::EscapeDataString($ssh.User)
                $encPath = [System.Uri]::EscapeDataString($TargetPath)
                $gwUri = "jetbrains-gateway://connect#host=$encHost&port=$($ssh.Port)&user=$encUser&projectPath=$encPath&type=ssh&deploy=true"
                if ($resolved.Code) {
                    $gwUri += "&ideCode=$($resolved.Code)"
                }
                Write-BridgeLog "[OPEN] requested=$reqNorm resolved=$($resolved.Id) alias=$Alias path=$TargetPath uri=$gwUri"
                Start-Process -FilePath $gwUri | Out-Null
                return $true
            }
        }
    } catch {
        Write-BridgeLog "[ERROR] Failed launching IDE ($($resolved.Id)): $($_.Exception.Message)"
        return $false
    }
    return $false
}

$script:SpeechSynth = $null
function Speak-Text([string]$Text) {
    if ([string]::IsNullOrWhiteSpace($Text)) { return }
    $clean = ($Text -replace '[\x00-\x1F\x7F]', ' ').Trim()
    if ($clean.Length -gt 500) { $clean = $clean.Substring(0, 500) }
    try {
        if (-not $script:SpeechSynth) {
            Add-Type -AssemblyName System.Speech -ErrorAction Stop
            $script:SpeechSynth = New-Object System.Speech.Synthesis.SpeechSynthesizer
        }
        $script:SpeechSynth.SpeakAsync($clean) | Out-Null
    } catch {}
}

function Send-HttpResponse($Stream, [int]$StatusCode, [string]$StatusText, [string]$Body) {
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
    $header = "HTTP/1.1 $StatusCode $StatusText`r`nContent-Type: text/plain; charset=utf-8`r`nContent-Length: $($bodyBytes.Length)`r`nConnection: close`r`n`r`n"
    $headerBytes = [System.Text.Encoding]::ASCII.GetBytes($header)
    $Stream.Write($headerBytes, 0, $headerBytes.Length)
    if ($bodyBytes.Length -gt 0) {
        $Stream.Write($bodyBytes, 0, $bodyBytes.Length)
    }
    $Stream.Flush()
}

function Handle-HttpClient($Client) {
    try {
        $Client.ReceiveTimeout = 2000
        $Client.SendTimeout = 2000
        $stream = $Client.GetStream()
        $mem = New-Object System.IO.MemoryStream
        $buf = New-Object byte[] 4096
        $headerEnd = -1
        while ($mem.Length -lt 16384) {
            $read = $stream.Read($buf, 0, $buf.Length)
            if ($read -le 0) { break }
            $mem.Write($buf, 0, $read)
            $curText = [System.Text.Encoding]::ASCII.GetString($mem.GetBuffer(), 0, [int]$mem.Length)
            $idx = $curText.IndexOf("`r`n`r`n")
            if ($idx -ge 0) {
                $headerEnd = $idx + 4
                break
            }
        }
        if ($headerEnd -lt 0) {
            Send-HttpResponse $stream 400 'Bad Request' "INVALID`n"
            return
        }
        $allBytes = $mem.ToArray()
        $headerText = [System.Text.Encoding]::ASCII.GetString($allBytes, 0, $headerEnd)
        $lines = $headerText -split "`r`n"
        $reqParts = $lines[0].Trim() -split '\s+'
        if ($reqParts.Count -lt 2) {
            Send-HttpResponse $stream 400 'Bad Request' "INVALID`n"
            return
        }
        $method = $reqParts[0].ToUpperInvariant()
        $rawTarget = $reqParts[1]
        $qIdx = $rawTarget.IndexOf('?')
        $reqPath = if ($qIdx -ge 0) { $rawTarget.Substring(0, $qIdx) } else { $rawTarget }
        $rawQuery = if ($qIdx -ge 0) { $rawTarget.Substring($qIdx + 1) } else { '' }

        $contentLength = 0
        foreach ($ln in $lines[1..($lines.Count - 1)]) {
            if ($ln -match '^(?i)Content-Length:\s*(\d+)') {
                $contentLength = [Math]::Min([int]$matches[1], 16384)
            }
        }
        $bodyBytes = New-Object System.IO.MemoryStream
        if ($allBytes.Length -gt $headerEnd) {
            $bodyBytes.Write($allBytes, $headerEnd, $allBytes.Length - $headerEnd)
        }
        while ($bodyBytes.Length -lt $contentLength) {
            $read = $stream.Read($buf, 0, [Math]::Min($buf.Length, $contentLength - [int]$bodyBytes.Length))
            if ($read -le 0) { break }
            $bodyBytes.Write($buf, 0, $read)
        }

        if ($method -eq 'GET' -and $reqPath -eq '/health') {
            Send-HttpResponse $stream 200 'OK' "OK`n"
        } elseif ($method -eq 'GET' -and $reqPath -eq '/ides') {
            $defIde = Get-DefaultHostIde
            $inst = Get-InstalledHostIdes
            $instStr = if ($inst.Count -gt 0) { $inst -join ',' } else { 'none' }
            Send-HttpResponse $stream 200 'OK' "default=$defIde`ninstalled=$instStr`n"
        } elseif ($method -eq 'GET' -and $reqPath -eq '/open') {
            $params = @{}
            if ($rawQuery) {
                foreach ($pair in ($rawQuery -split '&')) {
                    if (-not $pair) { continue }
                    $kv = $pair -split '=', 2
                    $k = [System.Uri]::UnescapeDataString(($kv[0] -replace '\+', ' '))
                    $v = if ($kv.Count -gt 1) { [System.Uri]::UnescapeDataString(($kv[1] -replace '\+', ' ')) } else { '' }
                    $params[$k] = $v
                }
            }
            $alias = if ($params.ContainsKey('alias')) { $params['alias'] } else { if ($Mode -eq 'server') { 'openrig-snowball' } else { 'openrig' } }
            $targetPath = if ($params.ContainsKey('path')) { $params['path'] } else { '/workspace' }
            $reqIde = if ($params.ContainsKey('ide')) { $params['ide'] } else { 'auto' }
            if (-not (Test-SafeAlias $alias) -or -not (Test-SafePath $targetPath) -or -not (Test-SafeIde $reqIde)) {
                Write-BridgeLog "[REJECTED_HTTP] alias='$alias' path='$targetPath' ide='$reqIde'"
                Send-HttpResponse $stream 400 'Bad Request' "INVALID`n"
            } elseif (Open-HostIde $alias $targetPath $reqIde) {
                Send-HttpResponse $stream 200 'OK' "OK`n"
            } else {
                Send-HttpResponse $stream 500 'Internal Server Error' "IDE_NOT_FOUND`n"
            }
        } elseif ($method -eq 'POST' -and ($reqPath -in '/say', '/voice', '/')) {
            $sayText = [System.Text.Encoding]::UTF8.GetString($bodyBytes.ToArray()).Trim()
            Speak-Text $sayText
            Send-HttpResponse $stream 200 'OK' "OK`n"
        } else {
            Send-HttpResponse $stream 404 'Not Found' "NOT_FOUND`n"
        }
    } catch {
        Write-BridgeLog "[HTTP_ERR] $($_.Exception.Message)"
    } finally {
        try { $Client.Close() } catch {}
    }
}

function Invoke-TriggerFile([string]$TriggerPath, [string]$Alias) {
    if (-not (Test-Path $TriggerPath)) { return }
    try {
        $rawLines = @(Get-Content -Path $TriggerPath -TotalCount 2 -ErrorAction SilentlyContinue)
        Remove-Item -Path $TriggerPath -Force -ErrorAction SilentlyContinue
        $targetPath = if ($rawLines.Count -ge 1 -and $rawLines[0]) { ([string]$rawLines[0]).Trim() } else { '/workspace' }
        if (-not $targetPath) { $targetPath = '/workspace' }
        $reqIde = if ($rawLines.Count -ge 2 -and $rawLines[1]) { ([string]$rawLines[1]).Trim() } else { 'auto' }
        if (-not $reqIde) { $reqIde = 'auto' }
        $null = Open-HostIde $Alias $targetPath $reqIde
    } catch {}
}

function Process-TriggerFiles {
    if (-not $WatchRoot -or -not (Test-Path $WatchRoot)) { return }
    if ($Mode -eq 'server') {
        foreach ($dir in [System.IO.Directory]::GetDirectories($WatchRoot)) {
            $user = Split-Path -Leaf $dir
            Invoke-TriggerFile (Join-Path $dir '.openrig-ide-trigger') "openrig-$user"
        }
    } else {
        Invoke-TriggerFile (Join-Path $WatchRoot '.openrig-ide-trigger') 'openrig'
    }
}

if ($ListIdes) {
    $defIde = Get-DefaultHostIde
    $inst = Get-InstalledHostIdes
    $instStr = if ($inst.Count -gt 0) { $inst -join ',' } else { 'none' }
    Write-Output "default=$defIde"
    Write-Output "installed=$instStr"
    exit 0
}

if ($OpenOnce) {
    $effAlias = if ($Alias) { $Alias } else { if ($Mode -eq 'server') { 'openrig-snowball' } else { 'openrig' } }
    $ok = Open-HostIde $effAlias $Path $Ide
    if ($ok) { exit 0 } else { exit 1 }
}

# Single-instance idempotency check
try {
    $probe = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
    if ($probe.StatusCode -eq 200 -and $probe.Content.Trim() -eq 'OK') {
        exit 0
    }
} catch {}

if (-not $PidFile) {
    $defaultDir = if ($Mode -eq 'server') { Join-Path (Split-Path -Parent $PSScriptRoot) 'sockets' } else { Join-Path $HOME '.openrig' }
    $PidFile = Join-Path $defaultDir 'ide-bridge.pid'
}
if (-not $LogFile) {
    $defaultLogDir = if ($Mode -eq 'server') { Join-Path (Split-Path -Parent $PSScriptRoot) 'logs' } else { Join-Path $HOME '.openrig' }
    $LogFile = Join-Path $defaultLogDir 'ide-bridge.log'
}

foreach ($f in @($PidFile, $LogFile)) {
    $d = Split-Path -Parent $f
    if ($d -and -not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
    }
}
Set-Content -Path $PidFile -Value $PID -Encoding Ascii -Force

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try {
    $listener.Start()
    Write-BridgeLog "Started OpenRig Host IDE Bridge (Mode=$Mode, Port=$Port, WatchRoot=$WatchRoot, PID=$PID)"
    $lastPoll = [DateTime]::UtcNow.AddSeconds(-1)
    while ($true) {
        while ($listener.Pending()) {
            $client = $listener.AcceptTcpClient()
            Handle-HttpClient $client
        }
        if (([DateTime]::UtcNow - $lastPoll).TotalMilliseconds -ge 250) {
            Process-TriggerFiles
            $lastPoll = [DateTime]::UtcNow
        }
        Start-Sleep -Milliseconds 150
    }
} finally {
    try { $listener.Stop() } catch {}
    try {
        if ($PidFile -and (Test-Path $PidFile)) {
            $cur = (Get-Content -Path $PidFile -ErrorAction SilentlyContinue).Trim()
            if ($cur -eq "$PID") { Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue }
        }
    } catch {}
    Write-BridgeLog "Stopped OpenRig Host IDE Bridge (PID=$PID)"
}
