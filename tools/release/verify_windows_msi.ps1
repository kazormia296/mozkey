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

$artifact = Get-Item -LiteralPath $Path -ErrorAction Stop
if ($artifact.PSIsContainer) {
  throw "MSI artifact path is a directory: $Path"
}
if ($artifact.Extension -ine ".msi") {
  throw "MSI artifact does not have an .msi extension: $Path"
}

# An MSI database is an OLE Compound File and must contain at least one
# 512-byte sector.  Checking both the size and the fixed file signature catches
# empty, truncated, and accidentally misnamed build outputs before upload.
if ($artifact.Length -lt 512) {
  throw "MSI artifact is too small: $Path"
}

$expectedSignature = [byte[]](
  0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1
)
$actualSignature = New-Object byte[] $expectedSignature.Length
$stream = [System.IO.File]::Open(
  $artifact.FullName,
  [System.IO.FileMode]::Open,
  [System.IO.FileAccess]::Read,
  [System.IO.FileShare]::Read
)
try {
  $bytesRead = $stream.Read(
    $actualSignature,
    0,
    $actualSignature.Length
  )
} finally {
  $stream.Dispose()
}

if ($bytesRead -ne $expectedSignature.Length) {
  throw "MSI artifact header is truncated: $Path"
}
for ($index = 0; $index -lt $expectedSignature.Length; ++$index) {
  if ($actualSignature[$index] -ne $expectedSignature[$index]) {
    throw "MSI artifact has an invalid compound-file signature: $Path"
  }
}

Write-Host "Verified MSI artifact: $($artifact.FullName) ($($artifact.Length) bytes)"

$runtime = Get-Item -LiteralPath $RuntimePath -ErrorAction Stop
if ($runtime.PSIsContainer) {
  throw "Zenz runtime path is a directory: $RuntimePath"
}

$expectedMachine = switch ($ExpectedRuntimeArchitecture) {
  "x64" { 0x8664 }
  "arm64" { 0xAA64 }
}
$runtimeStream = [System.IO.File]::Open(
  $runtime.FullName,
  [System.IO.FileMode]::Open,
  [System.IO.FileAccess]::Read,
  [System.IO.FileShare]::Read
)
$reader = New-Object System.IO.BinaryReader($runtimeStream)
try {
  if ($reader.ReadUInt16() -ne 0x5A4D) {
    throw "Zenz runtime does not have an MZ header: $RuntimePath"
  }
  $runtimeStream.Seek(0x3C, [System.IO.SeekOrigin]::Begin) | Out-Null
  $peOffset = $reader.ReadUInt32()
  if ($peOffset -lt 0x40 -or $peOffset -gt 0x01000000) {
    throw "Zenz runtime has an invalid PE offset: $RuntimePath"
  }
  $runtimeStream.Seek($peOffset, [System.IO.SeekOrigin]::Begin) | Out-Null
  if ($reader.ReadUInt32() -ne 0x00004550) {
    throw "Zenz runtime does not have a PE signature: $RuntimePath"
  }
  $actualMachine = $reader.ReadUInt16()
} finally {
  $reader.Dispose()
  $runtimeStream.Dispose()
}

if ($actualMachine -ne $expectedMachine) {
  throw (
    "Zenz runtime PE architecture mismatch: expected {0}, machine 0x{1:x4}" -f `
      $ExpectedRuntimeArchitecture, $actualMachine
  )
}

Write-Host (
  "Verified Zenz runtime architecture: {0} ({1})" -f `
    $ExpectedRuntimeArchitecture, $runtime.FullName
)
