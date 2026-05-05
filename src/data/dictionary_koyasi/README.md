# Koyasi Dictionary Data

This directory contains additional dictionary data and generated dictionary artifacts for this Mozc fork.

## Policy

- Do not edit upstream dictionary files directly when possible.
- Generated dictionary files are not committed by default.
- External dictionaries must be imported through scripts.
- Large dictionaries should be separated by profile, such as safe, developer, subculture, and max.

## Generated files

generated/ is ignored by Git. Run import scripts to regenerate files locally.

## Notes for Windows PowerShell

Generated dictionary files are UTF-8. Use the following command to inspect them:

```powershell
Get-Content -Encoding UTF8 src/data/dictionary_koyasi/generated/mozcdic-ut-safe.txt -TotalCount 10
```

## Merge-UT dictionary workflow

### 1. Generate merge-ut source dictionaries

Sample profile:

```powershell
.\tools\dictionary\import_merge_ut.ps1 -Profile sample -SampleLines 5000
```

Rich profile:

```powershell
.\tools\dictionary\import_merge_ut.ps1 -Profile rich -SampleLines 5000
```

Generated files are written under:

```text
src/data/dictionary_koyasi/generated/
```

These generated files are ignored by Git.

### 2. Generate profiled dictionaries

Daily profile:

```powershell
python tools/dictionary/profile_merge_ut.py --profile daily
```

Rich profile:

```powershell
python tools/dictionary/profile_merge_ut.py --profile rich
```

Profiled files are written under:

```text
src/data/dictionary_koyasi/generated/profiled/
```

### 3. Check profiled dictionaries

Daily profile check:

```powershell
python tools/dictionary/check_merge_ut_profile.py --profile daily
```

The positive checks must pass. Watch keys are informational and are used to inspect potentially risky readings.

### 4. Switch the active merge-ut profile

Use the tracked sample dictionary:

```powershell
.\tools\dictionary\use_merge_ut_profile.ps1 -Profile sample
```

Use the locally generated daily dictionary:

```powershell
.\tools\dictionary\use_merge_ut_profile.ps1 -Profile daily
```

The default committed state should use `sample`, because `daily`, `rich`, and `max` dictionaries are locally generated and are not committed.

### 5. Build with daily profile

```powershell
.\tools\dictionary\import_merge_ut.ps1 -Profile sample -SampleLines 5000
python tools/dictionary/profile_merge_ut.py --profile daily
python tools/dictionary/check_merge_ut_profile.py --profile daily
.\tools\dictionary\use_merge_ut_profile.ps1 -Profile daily

cd src
bazelisk build --config oss_windows --config release_build //data/dictionary_oss:dictionary_data
bazelisk build --config oss_windows --config release_build package
python build_tools/open.py bazel-bin/win32/installer/Mozc64.msi
cd ..
```

### 6. Return to default sample profile

Before committing unrelated work, return to the sample profile:

```powershell
.\tools\dictionary\use_merge_ut_profile.ps1 -Profile sample
```

Then confirm:

```powershell
git status --short
```

`src/data/dictionary_oss/BUILD.bazel` should not appear unless you intentionally changed it.

## Nico/Pixiv dictionary workflow

`dic-nico-intersection-pixiv` is imported as an additional delta dictionary for the daily local profile.

The generated delta is compared against the generated daily dictionary and the base Mozc dictionaries. Existing key/value pairs are skipped.

### 1. Download source dictionary

```powershell
.\tools\dictionary\import_nico_pixiv.ps1
```

### 2. Convert to Mozc system dictionary delta

```powershell
python tools/dictionary/convert_nico_pixiv.py
```

The generated file is:

```text
src/data/dictionary_koyasi/generated/profiled/dic-nico-pixiv-delta.txt
```

This file is ignored by Git.

### 3. Build daily with Nico/Pixiv delta

```powershell
.\tools\dictionary\import_merge_ut.ps1 -Profile sample -SampleLines 5000
python tools/dictionary/profile_merge_ut.py --profile daily
.\tools\dictionary\import_nico_pixiv.ps1
python tools/dictionary/convert_nico_pixiv.py
python tools/dictionary/check_merge_ut_profile.py --profile daily
.\tools\dictionary\use_merge_ut_profile.ps1 -Profile daily

cd src
bazelisk build --config oss_windows --config release_build package
python build_tools/open.py bazel-bin/win32/installer/Mozc64.msi
cd ..
```

Before committing unrelated work, return to the sample profile:

```powershell
.\tools\dictionary\use_merge_ut_profile.ps1 -Profile sample
```
