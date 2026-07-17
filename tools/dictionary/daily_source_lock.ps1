$ErrorActionPreference = "Stop"

$DailySourceLockPath = Join-Path $PSScriptRoot "daily_sources.lock.json"
if (-not (Test-Path -LiteralPath $DailySourceLockPath -PathType Leaf)) {
    throw "Daily dictionary source lock is missing: $DailySourceLockPath"
}

$DailySourceLock = Get-Content -Raw -Encoding UTF8 $DailySourceLockPath | ConvertFrom-Json
if ($DailySourceLock.schema_version -ne "mozkey.daily_dictionary_source_lock.v1") {
    throw "Unexpected daily dictionary source lock schema: $($DailySourceLock.schema_version)"
}

function Get-DailyLockedSource {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Id
    )

    $Property = $DailySourceLock.sources.PSObject.Properties[$Id]
    if ($null -eq $Property) {
        throw "Unknown daily dictionary source lock id: $Id"
    }
    return $Property.Value
}

function Get-DailyLockedPayload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceId,
        [Parameter(Mandatory = $true)]
        [string]$PayloadId
    )

    $Source = Get-DailyLockedSource -Id $SourceId
    $Property = $Source.payloads.PSObject.Properties[$PayloadId]
    if ($null -eq $Property) {
        throw "Unknown daily dictionary payload: $SourceId.$PayloadId"
    }
    return $Property.Value
}

function Assert-DailyLockedFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceId,
        [Parameter(Mandatory = $true)]
        [string]$PayloadId,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Locked dictionary payload is missing: $Path"
    }
    $Payload = Get-DailyLockedPayload -SourceId $SourceId -PayloadId $PayloadId
    $Expected = $Payload.sha256.ToLowerInvariant()
    $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "SHA-256 mismatch for $SourceId.$PayloadId. Expected $Expected, got $Actual"
    }
    Write-Host "Verified SHA-256: $Actual  $Path"
}

function Assert-DailyLockedGitRevision {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceId,
        [Parameter(Mandatory = $true)]
        [string]$RepositoryPath
    )

    $Source = Get-DailyLockedSource -Id $SourceId
    $Actual = (& git -C $RepositoryPath rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $Actual -ne $Source.revision) {
        throw "Git revision mismatch for $SourceId. Expected $($Source.revision), got $Actual"
    }
    Write-Host "Verified revision: $Actual  $SourceId"
    if ($Source.PSObject.Properties["tree"]) {
        $ActualTree = (& git -C $RepositoryPath rev-parse "HEAD^{tree}").Trim()
        if ($LASTEXITCODE -ne 0 -or $ActualTree -ne $Source.tree) {
            throw "Git tree mismatch for $SourceId. Expected $($Source.tree), got $ActualTree"
        }
        Write-Host "Verified tree: $ActualTree  $SourceId"
    }
}
