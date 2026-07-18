param(
    [ValidateSet("sample", "safe", "daily", "rich", "max")]
    [string]$Profile = "sample",
    [int]$SampleLines = 5000,
    [string]$BashPath = ""
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "daily_source_lock.ps1")

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$WorkRoot = Join-Path $RepoRoot "dist\dictionary\merge-ut"
$OutDir = Join-Path $RepoRoot "src\data\dictionary_koyasi\generated"
$MergeSource = Get-DailyLockedSource -Id "merge-ut-dictionaries"
$MergeRevision = $MergeSource.revision
$MergeRepo = Join-Path $WorkRoot "merge-ut-dictionaries-$($MergeRevision.Substring(0, 12))"

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

function Test-IsWindowsHost {
    if ($PSVersionTable.ContainsKey("Platform")) {
        return $PSVersionTable.Platform -eq "Win32NT"
    }

    return $env:OS -eq "Windows_NT"
}

function Test-BashPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return $false
    }

    try {
        $Output = & $Path -lc "printf MOZC_BASH_OK" 2>$null
        return ($LASTEXITCODE -eq 0 -and $Output -eq "MOZC_BASH_OK")
    } catch {
        return $false
    }
}

function Find-Bash {
    param(
        [string]$RequestedBashPath = ""
    )

    if ($RequestedBashPath) {
        if (Test-BashPath $RequestedBashPath) {
            return (Resolve-Path $RequestedBashPath).Path
        }
        throw "Specified bash is not usable: $RequestedBashPath"
    }

    if (Test-IsWindowsHost) {
        $BashCandidates = @(
            (Join-Path $RepoRoot "src\third_party\msys64\usr\bin\bash.exe"),
            (Join-Path $RepoRoot "src\third_party\msys64\bin\bash.exe"),
            "C:\Program Files\Git\bin\bash.exe",
            "C:\Program Files\Git\usr\bin\bash.exe",
            "C:\Program Files (x86)\Git\bin\bash.exe",
            "C:\Program Files (x86)\Git\usr\bin\bash.exe"
        )

        foreach ($Candidate in $BashCandidates) {
            if (Test-BashPath $Candidate) {
                return (Resolve-Path $Candidate).Path
            }
        }

        $PathBashes = @(Get-Command bash.exe -ErrorAction SilentlyContinue)
        foreach ($Command in $PathBashes) {
            $Source = $Command.Source

            # Do not select WSL bash launchers blindly. They depend on the
            # default WSL distribution and may resolve to docker-desktop,
            # which is not a normal Linux user environment.
            if ($Source -like "$env:WINDIR\System32\bash.exe" -or
                $Source -like "$env:LOCALAPPDATA\Microsoft\WindowsApps\bash.exe") {
                if (Test-BashPath $Source) {
                    return $Source
                }
                continue
            }

            if (Test-BashPath $Source) {
                return $Source
            }
        }

        throw "Usable bash was not found. Install Git for Windows, use Mozc's MSYS bash, or pass -BashPath explicitly."
    }

    $Command = Get-Command bash -ErrorAction SilentlyContinue
    if ($Command -and (Test-BashPath $Command.Source)) {
        return $Command.Source
    }

    foreach ($Candidate in @("/bin/bash", "/usr/bin/bash")) {
        if (Test-BashPath $Candidate) {
            return $Candidate
        }
    }

    throw "Usable bash was not found."
}

$BashPath = Find-Bash -RequestedBashPath $BashPath
Write-Host "Using bash: $BashPath"

if (-not (Test-Path (Join-Path $MergeRepo ".git"))) {
    Write-Host "Fetching pinned merge-ut-dictionaries revision..."
    New-Item -ItemType Directory -Force $MergeRepo | Out-Null
    git -C $MergeRepo init
    if ($LASTEXITCODE -ne 0) { throw "git init failed." }
    git -C $MergeRepo config core.autocrlf false
    if ($LASTEXITCODE -ne 0) { throw "git config failed." }
    git -C $MergeRepo remote add origin $MergeSource.repository
    if ($LASTEXITCODE -ne 0) { throw "git remote add failed." }
    git -C $MergeRepo fetch --depth 1 origin $MergeRevision
    if ($LASTEXITCODE -ne 0) { throw "git fetch failed." }
    git -C $MergeRepo checkout --detach FETCH_HEAD
    if ($LASTEXITCODE -ne 0) { throw "git checkout failed." }
} else {
    $Origin = (& git -C $MergeRepo remote get-url origin).Trim()
    if ($LASTEXITCODE -ne 0 -or $Origin -ne $MergeSource.repository) {
        throw "Pinned merge-ut checkout has an unexpected origin: $Origin"
    }
}

Assert-DailyLockedGitRevision -SourceId "merge-ut-dictionaries" -RepositoryPath $MergeRepo

# A prior run patches this generated checkout. Restore the one controlled file
# from the already verified immutable revision before applying the current
# profile. This checkout lives below ignored dist/dictionary, never in src/.
git -C $MergeRepo checkout -- src/merge/make.sh
if ($LASTEXITCODE -ne 0) { throw "failed to restore pinned make.sh." }

$MakePath = Join-Path $MergeRepo "src\merge\make.sh"
if (-not (Test-Path $MakePath)) {
    throw "make.sh was not found: $MakePath"
}

Assert-DailyLockedFile -SourceId "merge-ut-dictionaries" -PayloadId "make_script" -Path $MakePath
Assert-DailyLockedFile -SourceId "merge-ut-dictionaries" -PayloadId "merge_script" -Path (Join-Path $MergeRepo "src\merge\merge_dictionaries.py")

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

function Set-PinnedGitClone {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Content,
        [Parameter(Mandatory = $true)]
        [string]$SourceId,
        [Parameter(Mandatory = $true)]
        [string]$Directory,
        [Parameter(Mandatory = $true)]
        [string]$PayloadId
    )

    $Source = Get-DailyLockedSource -Id $SourceId
    $Payload = Get-DailyLockedPayload -SourceId $SourceId -PayloadId $PayloadId
    $Original = "git clone --depth 1 $($Source.repository)"
    if (-not $Content.Contains($Original)) {
        throw "Pinned clone site was not found for $SourceId"
    }
    $Replacement = @(
        "git init $Directory",
        "git -C $Directory config core.autocrlf false",
        "git -C $Directory remote add origin $($Source.repository)",
        "git -C $Directory fetch --depth 1 origin $($Source.revision)",
        "git -C $Directory checkout --detach $($Source.revision)",
        "printf '%s  %s\n' '$($Payload.sha256)' '$Directory/$($Payload.path)' | sha256sum -c - || exit 1"
    ) -join "`n"
    return $Content.Replace($Original, $Replacement)
}

Write-Host "Patching make.sh for profile: $EffectiveProfile"

$Content = Get-Content -Raw -Encoding UTF8 $MakePath

foreach ($Key in $Flags.Keys) {
    $Content = Set-MakeFlag -Content $Content -Name $Key -Enabled $Flags[$Key]
}

$Content = Set-PinnedGitClone -Content $Content `
    -SourceId "mozcdic-ut-place-names" `
    -Directory "mozcdic-ut-place-names" `
    -PayloadId "dictionary_bz2"
$Content = Set-PinnedGitClone -Content $Content `
    -SourceId "mozcdic-ut-sudachidict" `
    -Directory "mozcdic-ut-sudachidict" `
    -PayloadId "dictionary_bz2"
$Content = Set-PinnedGitClone -Content $Content `
    -SourceId "mozcdic-ut-personal-names" `
    -Directory "mozcdic-ut-personal-names" `
    -PayloadId "dictionary_bz2"

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

if ($Flags.place_names) {
    $PlaceRepo = Join-Path $MergeDir "mozcdic-ut-place-names"
    Assert-DailyLockedGitRevision -SourceId "mozcdic-ut-place-names" -RepositoryPath $PlaceRepo
    Assert-DailyLockedFile -SourceId "mozcdic-ut-place-names" -PayloadId "dictionary_bz2" -Path (Join-Path $PlaceRepo "mozcdic-ut-place-names.txt.bz2")
}
if ($Flags.sudachidict) {
    $SudachiRepo = Join-Path $MergeDir "mozcdic-ut-sudachidict"
    Assert-DailyLockedGitRevision -SourceId "mozcdic-ut-sudachidict" -RepositoryPath $SudachiRepo
    Assert-DailyLockedFile -SourceId "mozcdic-ut-sudachidict" -PayloadId "dictionary_bz2" -Path (Join-Path $SudachiRepo "mozcdic-ut-sudachidict.txt.bz2")
}
if ($Flags.personal_names) {
    $PersonalNamesRepo = Join-Path $MergeDir "mozcdic-ut-personal-names"
    Assert-DailyLockedGitRevision -SourceId "mozcdic-ut-personal-names" -RepositoryPath $PersonalNamesRepo
    Assert-DailyLockedFile -SourceId "mozcdic-ut-personal-names" -PayloadId "dictionary_bz2" -Path (Join-Path $PersonalNamesRepo "mozcdic-ut-personal-names.txt.bz2")
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
