# Copyright 2026 The Mozkey Authors

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("x64", "arm64")]
  [string]$Architecture,

  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$DestinationRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$requiredDlls = @(
  "msvcp140.dll",
  "msvcp140_1.dll",
  "msvcp140_2.dll",
  "vcruntime140.dll",
  "vcruntime140_1.dll"
)
$expectedRedistVersion = "14.50.35710"
$expectedFileVersion = "$expectedRedistVersion.0"
$expectedToolsetDirectoryPattern = '^Microsoft\.VC\d+\.CRT$'
$expectedToolsetDirectory = "Microsoft.VC145.CRT"

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path -LiteralPath $vswhere)) {
  throw "vswhere.exe was not found."
}

$installations = @(
  & $vswhere -all -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath |
    Where-Object {
      $_ -and (Test-Path -LiteralPath $_ -PathType Container)
    }
)
$sourceCandidates = @(
  foreach ($installation in $installations) {
    $architectureRoot = Join-Path $installation "VC\Redist\MSVC\$expectedRedistVersion\$Architecture"
    if (Test-Path -LiteralPath $architectureRoot -PathType Container) {
      Get-ChildItem -LiteralPath $architectureRoot -Directory |
        Where-Object { $_.Name -match $expectedToolsetDirectoryPattern } |
        ForEach-Object { $_.FullName }
    }
  }
)
if ($sourceCandidates.Count -ne 1) {
  throw "Expected exactly one pinned Microsoft CRT source directory, found $($sourceCandidates.Count)."
}
$sourceRoot = $sourceCandidates[0]
$sourceToolsetDirectory = Split-Path -Leaf $sourceRoot

if ($sourceToolsetDirectory -notmatch $expectedToolsetDirectoryPattern -or
    $sourceRoot -notmatch "\\$Architecture\\$sourceToolsetDirectory$") {
  throw "CRT source directory has an unexpected architecture or toolset: $sourceRoot"
}
if ((Split-Path -Leaf $DestinationRoot) -ne $expectedToolsetDirectory) {
  throw "CRT destination must use the canonical toolset directory ${expectedToolsetDirectory}: $DestinationRoot"
}

New-Item -ItemType Directory -Force $DestinationRoot | Out-Null
foreach ($dll in $requiredDlls) {
  $source = Join-Path $sourceRoot $dll
  if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Pinned CRT file is missing: $source"
  }

  $signature = Get-AuthenticodeSignature -LiteralPath $source
  if ($signature.Status -ne "Valid" -or
      $signature.SignerCertificate.Subject -notmatch "(?i)Microsoft") {
    throw "Pinned CRT file is not signed by Microsoft: $source"
  }

  $fileVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($source).FileVersion
  if ($fileVersion -ne $expectedFileVersion) {
    throw "Pinned CRT file has unexpected version: $source ($fileVersion)"
  }

  $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
  $destination = Join-Path $DestinationRoot $dll
  Copy-Item -LiteralPath $source -Destination $destination -Force
  $destinationHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($sourceHash -ne $destinationHash) {
    throw "CRT copy hash mismatch: $dll"
  }

  Write-Host "$dll version=$fileVersion sha256=$sourceHash signer=$($signature.SignerCertificate.Subject)"
}

Write-Host "Prepared exact Microsoft CRT $expectedRedistVersion/$sourceToolsetDirectory as $expectedToolsetDirectory for $Architecture"
