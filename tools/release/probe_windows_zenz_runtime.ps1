# Copyright 2026 The Mozkey Authors

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$RuntimePath,

  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$ModelPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$runtime = (Resolve-Path -LiteralPath $RuntimePath -ErrorAction Stop).Path
$model = (Resolve-Path -LiteralPath $ModelPath -ErrorAction Stop).Path
$runtimeInfo = Get-Item -LiteralPath $runtime -ErrorAction Stop
$modelInfo = Get-Item -LiteralPath $model -ErrorAction Stop
if ($runtimeInfo.PSIsContainer -or $modelInfo.PSIsContainer) {
  throw "Zenz runtime probe received a directory instead of a file."
}

function New-RandomHighPort {
  for ($attempt = 0; $attempt -lt 16; ++$attempt) {
    $reservation = [System.Net.Sockets.TcpListener]::new(
      [System.Net.IPAddress]::Loopback, 0
    )
    try {
      $reservation.Start()
      $port = ([System.Net.IPEndPoint]$reservation.LocalEndpoint).Port
    } finally {
      $reservation.Stop()
    }
    if ($port -ge 49152 -and $port -le 65535) {
      return $port
    }
  }
  throw "Could not allocate a random high loopback port."
}

function Get-ListeningConnections([int]$ProcessId) {
  @(
    Get-NetTCPConnection -OwningProcess $ProcessId -State Listen `
      -ErrorAction SilentlyContinue
  )
}

$port = New-RandomHighPort
$apiKeyBytes = New-Object byte[] 32
$apiKeyGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$apiKeyGenerator.GetBytes($apiKeyBytes)
$apiKey = [Convert]::ToBase64String($apiKeyBytes)
$probeRoot = Join-Path ([IO.Path]::GetTempPath()) `
  ("mozkey-zenz-probe-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $probeRoot | Out-Null
$stdoutPath = Join-Path $probeRoot "stdout.log"
$stderrPath = Join-Path $probeRoot "stderr.log"
$process = $null
$client = $null

try {
  $quotedModel = '"' + $model + '"'
  $arguments = @(
    "-m", $quotedModel,
    "-c", "256",
    "-t", "1",
    "--host", "127.0.0.1",
    "--port", "$port",
    "--api-key", $apiKey
  )
  $process = Start-Process `
    -FilePath $runtime `
    -ArgumentList $arguments `
    -WorkingDirectory $runtimeInfo.DirectoryName `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath

  $commandLine = (Get-CimInstance Win32_Process `
    -Filter "ProcessId = $($process.Id)").CommandLine
  if ($commandLine -notmatch "--host\s+127\.0\.0\.1" -or
      $commandLine -notmatch "--port\s+$port") {
    throw "llama-server command-line contract failed."
  }

  $handler = [System.Net.Http.HttpClientHandler]::new()
  $handler.UseProxy = $false
  $client = [System.Net.Http.HttpClient]::new($handler)
  $client.Timeout = [TimeSpan]::FromSeconds(3)
  $client.DefaultRequestHeaders.Authorization =
    [System.Net.Http.Headers.AuthenticationHeaderValue]::new(
      "Bearer", $apiKey
    )

  $deadline = (Get-Date).AddSeconds(120)
  $healthy = $false
  while ((Get-Date) -lt $deadline) {
    if ($process.HasExited) {
      throw "llama-server exited before the loopback probe completed."
    }

    $listeners = @(Get-ListeningConnections $process.Id)
    if ($listeners.Count -eq 1 -and
        $listeners[0].LocalAddress -eq "127.0.0.1" -and
        $listeners[0].LocalPort -eq $port) {
      try {
        $response = $client.GetAsync("http://127.0.0.1:$port/health").GetAwaiter().GetResult()
        try {
          if ([int]$response.StatusCode -eq 200) {
            $healthy = $true
            break
          }
        } finally {
          $response.Dispose()
        }
      } catch [System.Net.Http.HttpRequestException] {
        # The server can expose its listener before the health endpoint is
        # ready. Retry until the bounded deadline.
      } catch [System.Threading.Tasks.TaskCanceledException] {
        # The bounded client timeout is expected while the model is loading.
      }
    }
    Start-Sleep -Milliseconds 250
  }

  if (-not $healthy) {
    throw "llama-server did not expose a healthy loopback endpoint in time."
  }

  $listeners = @(Get-ListeningConnections $process.Id)
  if ($listeners.Count -ne 1 -or
      $listeners[0].LocalAddress -ne "127.0.0.1" -or
      $listeners[0].LocalPort -ne $port -or
      $listeners[0].LocalPort -eq 18080) {
    throw "llama-server exposed an unexpected listening endpoint."
  }

  Write-Host "Verified llama-server loopback health and process CLI contract."
} finally {
  if ($null -ne $client) {
    $client.Dispose()
  }
  $apiKeyGenerator.Dispose()
  if ($null -ne $process) {
    if (-not $process.HasExited) {
      Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    $process.Dispose()
  }
  if (Test-Path -LiteralPath $probeRoot) {
    Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}
