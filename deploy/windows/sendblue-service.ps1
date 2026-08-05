<#
.SYNOPSIS
    Installs the per-user startup task for OpenGemma's SendBlue integration.

.DESCRIPTION
    The task launches the source-owned jarvis-launcher.ps1 at user logon.  The
    launcher validates the server before registering a fresh tunnel URL, so a
    SendBlue webhook is never refreshed to an unavailable local origin.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'status')]
    [string] $Command = 'status'
)

$ErrorActionPreference = 'Stop'
$taskName = 'OpenGemma SendBlue'
$workspace = 'C:\Users\Marc\Documents\Codex\OpenGemma'
$launcher = Join-Path $workspace 'deploy\windows\jarvis-launcher.ps1'

function Install-Task {
    if (-not (Test-Path -LiteralPath $launcher)) {
        throw "Launcher not found at $launcher"
    }
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
        "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`""
    ) -WorkingDirectory $workspace
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
        -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal `
        -Description 'OpenGemma server and SendBlue webhook tunnel' | Out-Null
    Write-Output "Installed scheduled task '$taskName'."
}

function Uninstall-Task {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    Write-Output "Removed scheduled task '$taskName'."
}

function Show-Status {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Output "Task '$taskName' is not installed."
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    [pscustomobject]@{
        task = $taskName
        state = $task.State
        last_run = $info.LastRunTime
        last_result = ('0x{0:X8}' -f $info.LastTaskResult)
    }
}

switch ($Command) {
    'install' { Install-Task }
    'uninstall' { Uninstall-Task }
    'status' { Show-Status }
}
