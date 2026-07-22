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
foreach ($requiredProcessContract in @(
  "[System.Diagnostics.ProcessStartInfo]::new()",
  ".ArgumentList.Add(",
  ".WaitForExit()",
  ".ExitCode"
)) {
  if (-not $checkerSource.Contains($requiredProcessContract)) {
    throw "MSI extraction is missing process contract: $requiredProcessContract"
  }
}
if ($checkerSource.Contains("& msiexec.exe")) {
  throw "MSI extraction must wait on an explicit process handle."
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

$processProbeRoot = Join-Path $temporaryRoot (
  "mozkey process wait " + [guid]::NewGuid()
)
try {
  New-Item -ItemType Directory -Path $processProbeRoot | Out-Null
  $processProbe = Join-Path $processProbeRoot "child probe.ps1"
  $processMarker = Join-Path $processProbeRoot "child complete.txt"
  Set-Content -LiteralPath $processProbe -Encoding utf8 -Value @'
param([Parameter(Mandatory = $true)][string]$Marker)
Set-StrictMode -Version Latest
Start-Sleep -Milliseconds 250
Set-Content -LiteralPath $Marker -Encoding utf8 -Value "complete"
'@
  $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = (Get-Process -Id $PID).Path
  $startInfo.UseShellExecute = $false
  foreach ($argument in @("-NoProfile", "-File", $processProbe, $processMarker)) {
    [void]$startInfo.ArgumentList.Add($argument)
  }
  $process = [System.Diagnostics.Process]::Start($startInfo)
  if ($null -eq $process) {
    throw "Could not start the process-wait preflight child."
  }
  try {
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
      throw "Process-wait preflight child failed with exit code $($process.ExitCode)."
    }
  } finally {
    $process.Dispose()
  }
  if (-not (Test-Path -LiteralPath $processMarker -PathType Leaf)) {
    throw "Process-wait preflight continued before the child completed."
  }
} finally {
  Remove-Item -LiteralPath $processProbeRoot -Recurse -Force `
    -ErrorAction SilentlyContinue
}

Write-Host "Parsed $($trackedScripts.Count) PowerShell scripts and passed StrictMode status preflight."
