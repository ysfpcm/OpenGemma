<#
.SYNOPSIS
    Starts a public tunnel for a local OpenJarvis SendBlue webhook and registers it.

.DESCRIPTION
    This helper deliberately reads SendBlue credentials from User environment
    variables. It never writes credentials to the repository or logs them.
    It is intended for development only: LocalTunnel URLs are temporary and
    may show an interstitial page that third-party webhook providers cannot
    complete. Use a named Cloudflare Tunnel or authenticated ngrok endpoint for
    reliable production delivery.

.PARAMETER OpenJarvisUrl
    Local OpenJarvis base URL. Defaults to http://127.0.0.1:8000.

#>
[CmdletBinding()]
param(
    [string]$OpenJarvisUrl = 'http://127.0.0.1:8000',
    [string]$StateDirectory = (Join-Path $env:USERPROFILE '.openjarvis\shared')
)

$ErrorActionPreference = 'Stop'
$apiKey = [Environment]::GetEnvironmentVariable('SENDBLUE_API_KEY_ID', 'User')
$apiSecret = [Environment]::GetEnvironmentVariable('SENDBLUE_API_SECRET_KEY', 'User')

if ([string]::IsNullOrWhiteSpace($apiKey) -or [string]::IsNullOrWhiteSpace($apiSecret)) {
    throw 'Set SENDBLUE_API_KEY_ID and SENDBLUE_API_SECRET_KEY in the User environment before starting the tunnel.'
}

try {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$OpenJarvisUrl/health" | Out-Null
} catch {
    throw "OpenJarvis is not reachable at $OpenJarvisUrl. Start the server before the tunnel."
}

New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
$outputLog = Join-Path $StateDirectory 'sendblue-tunnel.out.log'
$errorLog = Join-Path $StateDirectory 'sendblue-tunnel.err.log'
$statePath = Join-Path $StateDirectory 'sendblue-tunnel.json'
Remove-Item -LiteralPath $outputLog, $errorLog -Force -ErrorAction SilentlyContinue

$cloudflaredPath = Join-Path $StateDirectory 'cloudflared.exe'
if (-not (Test-Path $cloudflaredPath)) {
    Write-Output "Downloading cloudflared..."
    Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $cloudflaredPath
}

$tunnelProcess = Start-Process -FilePath $cloudflaredPath -ArgumentList @('tunnel', '--url', $OpenJarvisUrl) -RedirectStandardOutput $outputLog -RedirectStandardError $errorLog -WindowStyle Hidden -PassThru

$tunnelUrl = $null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 2
    $match = Select-String -LiteralPath $errorLog -Pattern 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -Last 1
    if ($match) {
        $tunnelUrl = $match.Matches[0].Value
        break
    }
}
if (-not $tunnelUrl) { throw 'Cloudflare Tunnel did not provide a public URL within one minute. Check logs.' }

$webhookUrl = "$tunnelUrl/webhooks/sendblue"
$headers = @{
    'sb-api-key-id' = $apiKey
    'sb-api-secret-key' = $apiSecret
    'Content-Type' = 'application/json'
}
# POST appends webhooks, which leaves old quick-tunnel URLs active after every
# restart.  Read the current configuration and replace only the receive list.
# This retains any user-configured webhooks for other event types.
$current = Invoke-RestMethod -Method Get -Uri 'https://api.sendblue.co/api/account/webhooks' -Headers $headers -TimeoutSec 20
$webhooks = $current.webhooks
if ($null -eq $webhooks) { $webhooks = @{} }
$webhooks.receive = @($webhookUrl)
$body = @{ webhooks = $webhooks } | ConvertTo-Json -Depth 8 -Compress
$response = Invoke-RestMethod -Method Put -Uri 'https://api.sendblue.co/api/account/webhooks' -Headers $headers -Body $body -TimeoutSec 20

[ordered]@{
    updated_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    tunnel_url = $tunnelUrl
    webhook_url = $webhookUrl
    provider = 'cloudflared'
    cloudflared_pid = $tunnelProcess.Id
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Output "SendBlue webhook updated: $webhookUrl"
