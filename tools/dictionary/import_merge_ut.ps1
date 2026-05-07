param(
    [ValidateSet("sample", "safe", "daily", "rich", "max")]
    [string]$Profile = "sample",

    [int]$SampleLines = 5000
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$WorkRoot = Join-Path $RepoRoot "dist\dictionary\merge-ut"
$OutDir = Join-Path $RepoRoot "src\data\dictionary_koyasi\generated"
$MergeRepo = Join-Path $WorkRoot "merge-ut-dictionaries"

if ($Profile -eq "safe") {
    $EffectiveProfile = "sample"
} else {
    $EffectiveProfile = $Profile
}

Write-Host "Repo root: $RepoRoot"
Write-Host "Work root: $WorkRoot"
Write-Host "Output dir: $OutDir"
Write-Host "Profile: $EffectiveProfile"

New-Item -ItemType Directory -Force $WorkRoot | Out-Null
New-Item -ItemType Directory -Force $OutDir | Out-Null

function Find-Bash {
    if ($IsWindows) {
        $GitBashCandidates = @(
            "C:\Program Files\Git\bin\bash.exe",
            "C:\Program Files\Git\usr\bin\bash.exe",
            "C:\Program Files (x86)\Git\bin\bash.exe",
            "C:\Program Files (x86)\Git\usr\bin\bash.exe"
        )

        $BashPath = $GitBashCandidates |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1

        if ($BashPath) {
            return $BashPath
        }

        $Command = Get-Command bash.exe -ErrorAction SilentlyContinue
        if ($Command) {
            return $Command.Source
        }

        throw "Git Bash was not found. Install Git for Windows or make sure bash.exe is available in PATH."
    }

    $Command = Get-Command bash -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }

    if (Test-Path "/bin/bash") {
        return "/bin/bash"
    }

    if (Test-Path "/usr/bin/bash") {
        return "/usr/bin/bash"
    }

    throw "bash was not found. Install bash or make sure it is available in PATH."
}

$BashPath = Find-Bash

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

$Flags = @{
    alt_cannadic   = $false
    edict2         = $false
    jawiki         = $false
    neologd        = $false
    personal_names = $false
    place_names    = $false
    skk_jisyo      = $false
    sudachidict    = $false
    generate_latest = $false
}

switch ($EffectiveProfile) {
    "sample" {
        # Minimal and reproducible first profile.
        $Flags.place_names = $true
        $Flags.sudachidict = $true
    }

    "daily" {
        # Current daily baseline.
        # Keep this conservative until we add cost profiling.
        $Flags.place_names = $true
        $Flags.sudachidict = $true
    }

    "rich" {
        # Broad recall profile, but not full legacy/GPL-heavy set.
        $Flags.jawiki = $true
        $Flags.neologd = $true
        $Flags.personal_names = $true
        $Flags.place_names = $true
        $Flags.sudachidict = $true
    }

    "max" {
        # Experimental full-recall profile.
        $Flags.alt_cannadic = $true
        $Flags.edict2 = $true
        $Flags.jawiki = $true
        $Flags.neologd = $true
        $Flags.personal_names = $true
        $Flags.place_names = $true
        $Flags.skk_jisyo = $true
        $Flags.sudachidict = $true
    }

    default {
        throw "Unknown profile: $EffectiveProfile"
    }
}

Write-Host "Enabled dictionaries:"
foreach ($Key in $Flags.Keys | Sort-Object) {
    if ($Key -eq "generate_latest") {
        continue
    }

    if ($Flags[$Key]) {
        Write-Host "  + $Key"
    }
}

Write-Host "Disabled dictionaries:"
foreach ($Key in $Flags.Keys | Sort-Object) {
    if ($Key -eq "generate_latest") {
        continue
    }

    if (-not $Flags[$Key]) {
        Write-Host "  - $Key"
    }
}

function Set-MakeFlag {
    param(
        [string]$Content,
        [string]$Name,
        [bool]$Enabled
    )

    if ($Enabled) {
        $Replacement = "$Name=`"true`""
    } else {
        $Replacement = "#$Name=`"true`""
    }

    $Pattern = "(?m)^#?" + [System.Text.RegularExpressions.Regex]::Escape("$Name=`"true`"")
    return [System.Text.RegularExpressions.Regex]::Replace($Content, $Pattern, $Replacement)
}

Write-Host "Patching make.sh for profile: $EffectiveProfile"

$Content = Get-Content -Raw -Encoding UTF8 $MakePath

foreach ($Key in $Flags.Keys) {
    $Content = Set-MakeFlag -Content $Content -Name $Key -Enabled $Flags[$Key]
}

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($MakePath, $Content, $Utf8NoBom)

$MergeDir = Join-Path $MergeRepo "src\merge"

Write-Host "Running make.sh..."
Push-Location $MergeDir
try {
    & $BashPath -lc "bash ./make.sh"
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

if ($EffectiveProfile -eq "sample") {
    # Keep legacy file names used in the previous step.
    $OutFile = Join-Path $OutDir "mozcdic-ut-safe.txt"
    $SampleFile = Join-Path $OutDir "mozcdic-ut-sample.txt"
} else {
    $OutFile = Join-Path $OutDir "mozcdic-ut-$EffectiveProfile.txt"
    $SampleFile = Join-Path $OutDir "mozcdic-ut-$EffectiveProfile-sample.txt"
}

Copy-Item $Generated $OutFile -Force

$LineCount = (Get-Content -Encoding UTF8 $OutFile | Measure-Object -Line).Lines
$Size = (Get-Item $OutFile).Length

if ($LineCount -le 0) {
    throw "Generated dictionary is empty: $OutFile"
}

if ($SampleLines -gt 0) {
    [string[]]$Sample = @(Get-Content -Encoding UTF8 $OutFile -TotalCount $SampleLines)

    if ($Sample.Count -le 0) {
        throw "Generated sample dictionary would be empty: $OutFile"
    }

    [System.IO.File]::WriteAllLines($SampleFile, $Sample, $Utf8NoBom)
}

Write-Host ""
Write-Host "Generated full dictionary:"
Write-Host "  $OutFile"
Write-Host "Lines:"
Write-Host "  $LineCount"
Write-Host "Bytes:"
Write-Host "  $Size"

if ($SampleLines -gt 0) {
    $SampleLineCount = (Get-Content -Encoding UTF8 $SampleFile | Measure-Object -Line).Lines
    $SampleSize = (Get-Item $SampleFile).Length

    Write-Host ""
    Write-Host "Generated sample dictionary:"
    Write-Host "  $SampleFile"
    Write-Host "Lines:"
    Write-Host "  $SampleLineCount"
    Write-Host "Bytes:"
    Write-Host "  $SampleSize"
}

Write-Host ""
Write-Host "Done."
