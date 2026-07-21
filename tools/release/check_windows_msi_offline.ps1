# Copyright 2026 The Mozkey Authors

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$Path,

  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$RuntimePath,

  [Parameter(Mandatory = $true)]
  [ValidateSet("x64", "arm64")]
  [string]$ExpectedRuntimeArchitecture
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$artifact = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
$runtime = (Resolve-Path -LiteralPath $RuntimePath -ErrorAction Stop).Path
$verifyScript = Join-Path $PSScriptRoot "verify_windows_msi.ps1"

& $verifyScript `
  -Path $artifact `
  -RuntimePath $runtime `
  -ExpectedRuntimeArchitecture $ExpectedRuntimeArchitecture
if ($LASTEXITCODE -ne 0) {
  throw "MSI structural verification failed."
}

function Invoke-NetworkCheck {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Script,
    [Parameter(Mandatory = $true)]
    [string[]]$Arguments
  )

  & python $Script @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Offline binary check failed: $Script"
  }
}

Invoke-NetworkCheck `
  (Join-Path $repositoryRoot "tools\check_no_network_imports.py") `
  @("--root", (Join-Path $repositoryRoot "src\bazel-bin"))
Invoke-NetworkCheck `
  (Join-Path $repositoryRoot "tools\check_no_network_strings.py") `
  @("--root", (Join-Path $repositoryRoot "src\bazel-bin"))
Invoke-NetworkCheck `
  (Join-Path $repositoryRoot "tools\check_no_network_imports.py") `
  @($runtime)
Invoke-NetworkCheck `
  (Join-Path $repositoryRoot "tools\check_no_network_strings.py") `
  @($runtime)

$extractDir = Join-Path $env:RUNNER_TEMP ("mozkey-msi-offline-" + [guid]::NewGuid())
try {
  New-Item -ItemType Directory -Force $extractDir | Out-Null
  & msiexec.exe /a $artifact /qn "TARGETDIR=$extractDir"
  if ($LASTEXITCODE -ne 0) {
    throw "MSI administrative extraction failed."
  }

  Invoke-NetworkCheck `
    (Join-Path $repositoryRoot "tools\check_no_network_imports.py") `
    @("--root", $extractDir)
  Invoke-NetworkCheck `
    (Join-Path $repositoryRoot "tools\check_no_network_strings.py") `
    @("--root", $extractDir)
} finally {
  if (Test-Path -LiteralPath $extractDir) {
    Remove-Item -LiteralPath $extractDir -Recurse -Force -ErrorAction SilentlyContinue
  }
}

Write-Host "Verified MSI and extracted payload for offline runtime checks: $artifact"
