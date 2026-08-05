<#
.SYNOPSIS
    Durable unified launcher for OpenJarvis server and SendBlue tunnel.

.DESCRIPTION
    Starts the OpenJarvis server using the known-working Python interpreter,
    and then invokes the SendBlue tunnel script to register the webhook.
#>

$ErrorActionPreference = 'Stop'

$workspace = "C:\Users\Marc\Documents\Codex\OpenGemma"
$tunnelScript = Join-Path $workspace "deploy\windows\sendblue-tunnel.ps1"
$stateDirectory = Join-Path $env:USERPROFILE '.openjarvis\shared'
$serverRunner = Join-Path $workspace "deploy\windows\jarvis-server-runner.ps1"
$serverOutLog = Join-Path $stateDirectory 'openjarvis-server.out.log'
$serverErrLog = Join-Path $stateDirectory 'openjarvis-server.err.log'

if (-not (Test-Path $serverRunner)) {
    throw "Server runner not found at $serverRunner"
}

New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
try {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:8000/health" | Out-Null
    $healthy = $true
} catch {
    $healthy = $false
}

if (-not $healthy) {
    Write-Output "Starting OpenJarvis server..."
    Remove-Item -LiteralPath $serverOutLog, $serverErrLog -Force -ErrorAction SilentlyContinue
    # The Codex desktop shell can expose both Path and PATH, which makes
    # Start-Process fail before it creates a child process. ``start /b`` avoids
    # that PowerShell bug while preserving a background, windowless child.
    $launchCommand = 'start "" /b powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" 1> "{1}" 2> "{2}"' -f $serverRunner, $serverOutLog, $serverErrLog
    & $env:ComSpec /d /s /c $launchCommand
}

Write-Output "Waiting for server to be healthy..."
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:8000/health" | Out-Null
        $healthy = $true
        break
    } catch {
        $healthy = $false
    }
}

if (-not $healthy) {
    throw "Server did not become healthy within 30 seconds."
}

Write-Output "Starting SendBlue tunnel..."
powershell.exe -ExecutionPolicy Bypass -File $tunnelScript

Write-Output "OpenJarvis and SendBlue tunnel are running."
