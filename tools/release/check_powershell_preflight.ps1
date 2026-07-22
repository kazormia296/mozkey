# Copyright 2026 The Mozkey Authors

[CmdletBinding()]
param(
  [Parameter(Mandatory = $false)]
  [string]$Repository = (Join-Path $PSScriptRoot "..\..")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path -LiteralPath $Repository -ErrorAction Stop).Path
$trackedScripts = @(& git -C $repositoryRoot ls-files -- "*.ps1")
if ($LASTEXITCODE -ne 0 -or $trackedScripts.Count -eq 0) {
  throw "Could not enumerate tracked PowerShell scripts."
}

foreach ($relativePath in $trackedScripts) {
  $path = Join-Path $repositoryRoot $relativePath
  $tokens = $null
  $parseErrors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile(
    $path,
    [ref]$tokens,
    [ref]$parseErrors
  )
  if ($parseErrors.Count -ne 0) {
    $details = ($parseErrors | ForEach-Object { $_.Message }) -join "; "
    throw "PowerShell syntax error in ${relativePath}: ${details}"
  }
}

$msiChecker = Join-Path $repositoryRoot "tools\release\check_windows_msi_offline.ps1"
$checkerSource = Get-Content -LiteralPath $msiChecker -Raw -ErrorAction Stop
$scriptVerification = $checkerSource.Split(
  "function Invoke-NetworkCheck",
  [System.StringSplitOptions]::None
)[0]
if ($scriptVerification -notmatch 'if \(-not \$\?\)') {
  throw "MSI script verification must consume the PowerShell success status."
}
if ($scriptVerification -match '\$LASTEXITCODE') {
  throw "MSI script verification reads an unset native exit code."
}

$temporaryRoot = if ($env:RUNNER_TEMP) {
  $env:RUNNER_TEMP
} else {
  [System.IO.Path]::GetTempPath()
}
$strictModeProbe = Join-Path $temporaryRoot "mozkey-strictmode-success.ps1"
try {
Set-Content -LiteralPath $strictModeProbe -Encoding utf8 -Value @'
Set-StrictMode -Version Latest
$null = $true
'@
  & {
    Set-StrictMode -Version Latest
    Remove-Variable -Name LASTEXITCODE -Scope Local -Force -ErrorAction SilentlyContinue
    & $strictModeProbe
    if (-not $?) {
      throw "Successful PowerShell child script reported failure."
    }
  }
} finally {
  Remove-Item -LiteralPath $strictModeProbe -Force -ErrorAction SilentlyContinue
}

Write-Host "Parsed $($trackedScripts.Count) PowerShell scripts and passed StrictMode status preflight."
