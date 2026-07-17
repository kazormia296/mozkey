param(
  [switch]$SkipDownload
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "daily_source_lock.ps1")

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$OutDir = Join-Path $RepoRoot "src\data\dictionary_koyasi\generated\personal_names"
$Bz2 = Join-Path $OutDir "mozcdic-ut-personal-names.txt.bz2"
$Txt = Join-Path $OutDir "mozcdic-ut-personal-names.txt"
$SourceId = "mozcdic-ut-personal-names"
$Payload = Get-DailyLockedPayload -SourceId $SourceId -PayloadId "dictionary_bz2"
$Url = $Payload.url
$Bz2Download = "$Bz2.download"
$TxtDownload = "$Txt.download"

New-Item -ItemType Directory -Force $OutDir | Out-Null

if (-not $SkipDownload) {
  Write-Host "Downloading mozcdic-ut personal names dictionary..."
  Write-Host "  $Url"
  try {
    Invoke-WebRequest -Uri $Url -OutFile $Bz2Download
    Assert-DailyLockedFile -SourceId $SourceId -PayloadId "dictionary_bz2" -Path $Bz2Download
    Move-Item -LiteralPath $Bz2Download -Destination $Bz2 -Force
  } finally {
    if (Test-Path -LiteralPath $Bz2Download) {
      Remove-Item -LiteralPath $Bz2Download -Force
    }
  }
} else {
  Write-Host "Skipping download."
}

if (-not (Test-Path $Bz2)) {
  throw "bz2 file does not exist: $Bz2"
}

Assert-DailyLockedFile -SourceId $SourceId -PayloadId "dictionary_bz2" -Path $Bz2

$env:MOZKEY_PERSONAL_NAMES_BZ2 = (Resolve-Path $Bz2).Path
$env:MOZKEY_PERSONAL_NAMES_TXT = $TxtDownload

$PythonCode = "import bz2, os, pathlib; src = pathlib.Path(os.environ['MOZKEY_PERSONAL_NAMES_BZ2']); dst = pathlib.Path(os.environ['MOZKEY_PERSONAL_NAMES_TXT']); dst.write_bytes(bz2.decompress(src.read_bytes())); print(f'wrote: {dst}'); print(f'bytes: {dst.stat().st_size}')"

try {
  python -c $PythonCode
  if ($LASTEXITCODE -ne 0) {
    throw "failed to decompress mozcdic-ut personal names dictionary."
  }
  Assert-DailyLockedFile -SourceId $SourceId -PayloadId "dictionary_txt" -Path $TxtDownload
  Move-Item -LiteralPath $TxtDownload -Destination $Txt -Force
} finally {
  if (Test-Path -LiteralPath $TxtDownload) {
    Remove-Item -LiteralPath $TxtDownload -Force
  }
}

Write-Host ""
Write-Host "Personal names dictionary is ready:"
Write-Host "  $Txt"
