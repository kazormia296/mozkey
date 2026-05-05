param(
    [ValidateSet("safe")]
    [string]$Profile = "safe"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$WorkRoot = Join-Path $RepoRoot "dist\dictionary\merge-ut"
$OutDir = Join-Path $RepoRoot "src\data\dictionary_koyasi\generated"
$MergeRepo = Join-Path $WorkRoot "merge-ut-dictionaries"

Write-Host "Repo root: $RepoRoot"
Write-Host "Work root: $WorkRoot"
Write-Host "Output dir: $OutDir"

New-Item -ItemType Directory -Force $WorkRoot | Out-Null
New-Item -ItemType Directory -Force $OutDir | Out-Null

$GitBashCandidates = @(
    "C:\Program Files\Git\bin\bash.exe",
    "C:\Program Files\Git\usr\bin\bash.exe",
    "C:\Program Files (x86)\Git\bin\bash.exe",
    "C:\Program Files (x86)\Git\usr\bin\bash.exe"
)

$BashPath = $GitBashCandidates |
    Where-Object { Test-Path $_ } |
    Select-Object -First 1

if (-not $BashPath) {
    throw "Git Bash was not found. Install Git for Windows and make sure C:\Program Files\Git\bin\bash.exe exists."
}

Write-Host "Using bash: $BashPath"

if (-not (Test-Path $MergeRepo)) {
    Write-Host "Cloning merge-ut-dictionaries..."
    git clone --depth 1 https://github.com/utuhiro78/merge-ut-dictionaries.git $MergeRepo
    if ($LASTEXITCODE -ne 0) {
        throw "git clone failed."
    }
} else {
    Write-Host "Updating merge-ut-dictionaries..."
    git -C $MergeRepo pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "git pull failed."
    }
}

$MakePath = Join-Path $MergeRepo "src\merge\make.sh"
if (-not (Test-Path $MakePath)) {
    throw "make.sh was not found: $MakePath"
}

Write-Host "Patching make.sh for profile: $Profile"

$Content = Get-Content -Raw -Encoding UTF8 $MakePath

# Safe profile:
#   enable:
#     - place_names
#     - sudachidict
#   disable:
#     - alt_cannadic
#     - edict2
#     - jawiki
#     - neologd
#     - personal_names
#     - skk_jisyo
#
# This keeps the first import conservative.
$Content = $Content -replace '#?alt_cannadic="true"', '#alt_cannadic="true"'
$Content = $Content -replace '#?edict2="true"', '#edict2="true"'
$Content = $Content -replace '#?jawiki="true"', '#jawiki="true"'
$Content = $Content -replace '#?neologd="true"', '#neologd="true"'
$Content = $Content -replace '#?personal_names="true"', '#personal_names="true"'
$Content = $Content -replace '#?skk_jisyo="true"', '#skk_jisyo="true"'
$Content = $Content -replace '#?place_names="true"', 'place_names="true"'
$Content = $Content -replace '#?sudachidict="true"', 'sudachidict="true"'
$Content = $Content -replace '#?generate_latest="true"', '#generate_latest="true"'

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($MakePath, $Content, $Utf8NoBom)

$MergeDir = Join-Path $MergeRepo "src\merge"

Write-Host "Running make.sh..."
Push-Location $MergeDir
try {
    & $BashPath -lc "sh make.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "make.sh failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$Generated = Join-Path $MergeDir "mozcdic-ut.txt"
if (-not (Test-Path $Generated)) {
    throw "mozcdic-ut.txt was not generated."
}

$OutFile = Join-Path $OutDir "mozcdic-ut-safe.txt"
Copy-Item $Generated $OutFile -Force

$LineCount = (Get-Content $OutFile | Measure-Object -Line).Lines
$Size = (Get-Item $OutFile).Length

Write-Host ""
Write-Host "Generated:"
Write-Host "  $OutFile"
Write-Host "Lines:"
Write-Host "  $LineCount"
Write-Host "Bytes:"
Write-Host "  $Size"
Write-Host ""
Write-Host "Done."
