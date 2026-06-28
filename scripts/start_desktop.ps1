[CmdletBinding()]
param(
    [int]$BackendPort = 3018,
    [int]$FrontendPort = 3011
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$StateDir = Join-Path $Root '.desktop'
$PidFile = Join-Path $StateDir 'dev.pid'
$DevScript = Join-Path $Root 'dev.ps1'
$FrontendUrl = "http://localhost:$FrontendPort"

New-Item -ItemType Directory -Path $StateDir -Force | Out-Null

function Test-PortListening($port) {
    $listeners = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    return [bool]$listeners
}

function Test-ProcessAlive($CandidateProcessId) {
    if (-not $CandidateProcessId) { return $false }
    try {
        [System.Diagnostics.Process]::GetProcessById([int]$CandidateProcessId) | Out-Null
        return $true
    } catch {
        return $false
    }
}

$existingPid = $null
if (Test-Path $PidFile) {
    $existingPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
}

$backendListening = Test-PortListening $BackendPort
$frontendListening = Test-PortListening $FrontendPort

if ((Test-ProcessAlive $existingPid) -and $backendListening -and $frontendListening) {
    Start-Process $FrontendUrl
    exit 0
}

if ($existingPid -and (Test-ProcessAlive $existingPid) -and (-not ($backendListening -and $frontendListening))) {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$ps = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $ps) {
    $ps = (Get-Command powershell.exe -ErrorAction Stop).Source
}

$args = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-NoExit',
    '-File', $DevScript,
    '-BackendPort', $BackendPort,
    '-FrontendPort', $FrontendPort
)

$proc = Start-Process -FilePath $ps -ArgumentList $args -WorkingDirectory $Root -WindowStyle Normal -PassThru
$proc.Id | Out-File -FilePath $PidFile -Encoding ascii -Force

for ($i = 0; $i -lt 60; $i++) {
    if (Test-PortListening $FrontendPort) { break }
    Start-Sleep -Milliseconds 500
}

Start-Process $FrontendUrl
