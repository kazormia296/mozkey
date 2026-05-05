param()

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$OutDir = Join-Path $RepoRoot "src\data\dictionary_koyasi\generated\nico_pixiv"
$OutFile = Join-Path $OutDir "dic-nico-intersection-pixiv-google.txt"

New-Item -ItemType Directory -Force $OutDir | Out-Null

$Url = "https://raw.githubusercontent.com/ncaq/dic-nico-intersection-pixiv/master/public/dic-nico-intersection-pixiv-google.txt"

Write-Host "Downloading:"
Write-Host "  $Url"
Write-Host "Output:"
Write-Host "  $OutFile"

Invoke-WebRequest -Uri $Url -OutFile $OutFile

$LineCount = (Get-Content -Encoding UTF8 $OutFile | Measure-Object -Line).Lines
$Size = (Get-Item $OutFile).Length

Write-Host ""
Write-Host "Downloaded:"
Write-Host "  lines: $LineCount"
Write-Host "  bytes: $Size"
