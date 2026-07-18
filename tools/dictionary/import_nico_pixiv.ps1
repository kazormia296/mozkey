param()

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "daily_source_lock.ps1")

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$OutDir = Join-Path $RepoRoot "src\data\dictionary_koyasi\generated\nico_pixiv"
$OutFile = Join-Path $OutDir "dic-nico-intersection-pixiv-google.txt"
$SourceId = "dic-nico-intersection-pixiv"
$Payload = Get-DailyLockedPayload -SourceId $SourceId -PayloadId "dictionary"

New-Item -ItemType Directory -Force $OutDir | Out-Null

$Url = $Payload.url
$Download = "$OutFile.download"

Write-Host "Downloading:"
Write-Host "  $Url"
Write-Host "Output:"
Write-Host "  $OutFile"

try {
    Invoke-WebRequest -Uri $Url -OutFile $Download
    Assert-DailyLockedFile -SourceId $SourceId -PayloadId "dictionary" -Path $Download
    Move-Item -LiteralPath $Download -Destination $OutFile -Force
} finally {
    if (Test-Path -LiteralPath $Download) {
        Remove-Item -LiteralPath $Download -Force
    }
}

$LineCount = (Get-Content -Encoding UTF8 $OutFile | Measure-Object -Line).Lines
$Size = (Get-Item $OutFile).Length

Write-Host ""
Write-Host "Downloaded:"
Write-Host "  lines: $LineCount"
Write-Host "  bytes: $Size"
