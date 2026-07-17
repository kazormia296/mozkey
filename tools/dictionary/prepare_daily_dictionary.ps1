param(
    [int]$SampleLines = 5000,
    [switch]$SkipDownload,
    [switch]$ReleaseApprovedOnly,
    [string]$BashPath = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

Write-Host "Repo root: $RepoRoot"
Write-Host ""

if (-not $SkipDownload) {
    Write-Host "Step 1: Import merge-ut sample profile..."
    $ImportMergeUtArgs = @{
        Profile = "sample"
        SampleLines = $SampleLines
    }

    if ($BashPath) {
        $ImportMergeUtArgs.BashPath = $BashPath
    }

    & (Join-Path $RepoRoot "tools\dictionary\import_merge_ut.ps1") @ImportMergeUtArgs

    Write-Host ""
    if ($ReleaseApprovedOnly) {
        Write-Host "Step 2: Exclude nico/pixiv (local evaluation only)..."
    } else {
        Write-Host "Step 2: Import pinned nico/pixiv dictionary for local evaluation..."
        & (Join-Path $RepoRoot "tools\dictionary\import_nico_pixiv.ps1")
    }

    Write-Host ""
    Write-Host "Step 2b: Import personal names dictionary..."
    & (Join-Path $RepoRoot "tools\dictionary\import_personal_names.ps1")
} else {
    Write-Host "Skipping downloads."
}

Write-Host ""
Write-Host "Step 3: Generate merge-ut daily profile..."
python (Join-Path $RepoRoot "tools\dictionary\profile_merge_ut.py") --profile daily
if ($LASTEXITCODE -ne 0) {
    throw "profile_merge_ut.py failed."
}

Write-Host ""
if ($ReleaseApprovedOnly) {
    Write-Host "Step 4: Keep nico/pixiv out of the public release profile..."
} else {
    Write-Host "Step 4: Convert pinned nico/pixiv delta for local evaluation..."
    python (Join-Path $RepoRoot "tools\dictionary\convert_nico_pixiv.py")
    if ($LASTEXITCODE -ne 0) { throw "convert_nico_pixiv.py failed." }
}

Write-Host ""
Write-Host "Step 4b: Profile personal names dictionary..."
$PersonalNamesArgs = @((Join-Path $RepoRoot "tools\dictionary\profile_personal_names.py"))
if ($ReleaseApprovedOnly) {
    # Never let a stale local-evaluation delta influence a public profile.
    $ExcludedNicoPath = Join-Path $RepoRoot "dist\dictionary\release-approved-only\nico-pixiv-excluded.txt"
    $PersonalNamesArgs += @("--nico-pixiv-delta", $ExcludedNicoPath)
}
python @PersonalNamesArgs
if ($LASTEXITCODE -ne 0) { throw "profile_personal_names.py failed." }

Write-Host ""
Write-Host "Step 5: Check daily profile..."
python (Join-Path $RepoRoot "tools\dictionary\check_merge_ut_profile.py") --profile daily
if ($LASTEXITCODE -ne 0) {
    throw "check_merge_ut_profile.py failed."
}

Write-Host ""
Write-Host "Step 6: Generate syntax guard dictionary..."
python (Join-Path $RepoRoot "tools\dictionary\generate_syntax_guard_dictionary.py")
if ($LASTEXITCODE -ne 0) {
    throw "generate_syntax_guard_dictionary.py failed."
}

Write-Host ""
if ($ReleaseApprovedOnly) {
    Write-Host "Step 7: Switch active profile to release-approved-only..."
    & (Join-Path $RepoRoot "tools\dictionary\use_merge_ut_profile.ps1") -Profile release
} else {
    Write-Host "Step 7: Switch active profile to daily local evaluation..."
    & (Join-Path $RepoRoot "tools\dictionary\use_merge_ut_profile.ps1") -Profile daily
}

Write-Host ""
if ($ReleaseApprovedOnly) {
    Write-Host "Release-approved-only daily dictionary is ready."
} else {
    Write-Host "Local-evaluation daily dictionary is ready."
}
Write-Host ""
Write-Host "Next build commands:"
if ($ReleaseApprovedOnly) {
    Write-Host "  cd src"
    Write-Host "  bazelisk build --config oss_linux --config release_build --define=mozkey_dictionary_profile=release-approved-only package"
    Write-Host "  cd .."
} else {
    Write-Host "  cd src"
    Write-Host "  bazelisk build --config oss_windows --config release_build package"
    Write-Host "  python build_tools/open.py bazel-bin/win32/installer/Mozc64.msi"
    Write-Host "  cd .."
    Write-Host ""
    Write-Host "Before committing unrelated work, run:"
    Write-Host '  .\tools\dictionary\use_merge_ut_profile.ps1 -Profile sample'
}
