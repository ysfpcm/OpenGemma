<#
.SYNOPSIS
    Runs OpenJarvis with the bundled Python runtime after a system Python loss.

.DESCRIPTION
    The project virtual environment was created against a user-installed
    Python, which may disappear after an OS or Python update.  This runner uses
    Codex's bundled Python and the existing virtualenv site packages.  It is
    intentionally foreground-only so the launcher/Scheduled Task can supervise
    it and retain an error log.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$workspace = 'C:\Users\Marc\Documents\Codex\OpenGemma'
$userProfile = [Environment]::GetEnvironmentVariable('USERPROFILE', 'User')
if ([string]::IsNullOrWhiteSpace($userProfile)) {
    # A clean scheduled-task environment may omit USERPROFILE.  This source
    # tree lives beneath Documents\Codex, so its owning profile is still
    # deterministically recoverable without placing secrets in the task.
    $userProfile = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $workspace))
}
$env:USERPROFILE = $userProfile
$pythonExe = Join-Path $userProfile '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$sitePackages = Join-Path $workspace '.venv\Lib\site-packages'
$sourceDirectory = Join-Path $workspace 'src'

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Bundled Python runtime not found at $pythonExe"
}
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "OpenJarvis dependencies not found at $sitePackages"
}

$envFile = Join-Path $userProfile '.openjarvis\cloud-keys.env'
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([^#=]+)\s*=\s*(.*)$') {
            Set-Item -Path "Env:\$($matches[1])" -Value $matches[2]
        }
    }
}

$env:PYTHONPATH = "$sourceDirectory;$sitePackages"
& $pythonExe -c 'import fastapi, httpx, uvicorn; import openjarvis'
if ($LASTEXITCODE -ne 0) { throw 'OpenJarvis runtime import validation failed.' }
& $pythonExe -m openjarvis.cli serve --host 127.0.0.1 --port 8000
exit $LASTEXITCODE
