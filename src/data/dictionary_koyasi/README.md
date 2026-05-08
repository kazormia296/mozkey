# Koyasi Dictionary Data

This directory contains additional dictionary data and generated dictionary artifacts for this Mozc fork.

## Policy

- Do not edit upstream dictionary files directly when possible.
- Generated dictionary files are not committed by default.
- External dictionaries must be imported through scripts.
- Large dictionaries should be separated by profile, such as `sample`, `daily`, `rich`, and `max`.
- The default committed Bazel profile should remain `sample` because `daily`, `rich`, and `max` depend on locally generated files.

## Generated files

`generated/` is ignored by Git. Run import and preparation scripts to regenerate files locally.

Main generated files:

```text
src/data/dictionary_koyasi/generated/mozcdic-ut-safe.txt
src/data/dictionary_koyasi/generated/mozcdic-ut-rich.txt
src/data/dictionary_koyasi/generated/profiled/mozcdic-ut-daily.txt
src/data/dictionary_koyasi/generated/profiled/mozcdic-ut-rich.txt
src/data/dictionary_koyasi/generated/profiled/dic-nico-pixiv-delta.txt
src/data/dictionary_koyasi/koyasi-syntax-guard.txt
```

These generated dictionary files are created locally and are not committed.

The small syntax guard dictionary is committed separately:
src/data/dictionary_koyasi/koyasi-syntax-guard.txt

## Notes for Windows PowerShell

Generated dictionary files are UTF-8. Use `-Encoding UTF8` when inspecting them with PowerShell:

```powershell
Get-Content -Encoding UTF8 src/data/dictionary_koyasi/generated/mozcdic-ut-safe.txt -TotalCount 10
Get-Content -Encoding UTF8 src/data/dictionary_koyasi/generated/profiled/koyasi-syntax-guard.txt -TotalCount 30
```

## Daily dictionary preparation workflow

The recommended way to prepare the enhanced daily dictionary is:

```powershell
.\tools\dictionary\prepare_daily_dictionary.ps1
```

If external source dictionaries have already been downloaded, use:

```powershell
.\tools\dictionary\prepare_daily_dictionary.ps1 -SkipDownload
```

This script performs the following steps:

1. Import merge-ut source dictionaries.
2. Import `dic-nico-intersection-pixiv`.
3. Generate the merge-ut daily profile.
4. Convert the nico/pixiv delta dictionary.
5. Check the daily profile.
6. Generate the syntax guard dictionary.
7. Switch the active local dictionary profile to `daily`.

The daily local dictionary target currently consists of:

```text
generated/profiled/dic-nico-pixiv-delta.txt
generated/profiled/koyasi-syntax-guard.txt
generated/profiled/mozcdic-ut-daily.txt
```

After preparing the daily dictionary, build Mozc from `src/`:

```powershell
cd src
bazelisk build --config oss_windows --config release_build //data/dictionary_oss:dictionary_data
bazelisk build --config oss_windows --config release_build package
python build_tools/open.py bazel-bin/win32/installer/Mozc64.msi
cd ..
```

Before committing unrelated work, return to the tracked sample profile:

```powershell
.\tools\dictionary\use_merge_ut_profile.ps1 -Profile sample
```

Then confirm:

```powershell
git status --short
```

`src/data/dictionary_oss/BUILD.bazel` should not appear unless you intentionally changed it.

## Merge-UT dictionary workflow

`merge-ut-dictionaries` is used as a source for large external dictionary data.

### Generate merge-ut source dictionaries

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

### Generate profiled dictionaries

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

### Check profiled dictionaries

Daily profile check:

```powershell
python tools/dictionary/check_merge_ut_profile.py --profile daily
```

The positive checks must pass. Watch keys are informational and are used to inspect potentially risky readings.

### Switch the active merge-ut profile

Use the tracked sample dictionary:

```powershell
.\tools\dictionary\use_merge_ut_profile.ps1 -Profile sample
```

Use the locally generated daily dictionary:

```powershell
.\tools\dictionary\use_merge_ut_profile.ps1 -Profile daily
```

The default committed state should use `sample`, because `daily`, `rich`, and `max` dictionaries are locally generated and are not committed.

## Nico/Pixiv dictionary workflow

`dic-nico-intersection-pixiv` is imported as an additional delta dictionary for the daily local profile.

The generated delta is compared against the generated daily dictionary and the base Mozc dictionaries. Existing key/value pairs are skipped.

The nico/pixiv converter applies a tiered cost policy. Compact modern compounds such as `ママ活` are intentionally stronger than general long-tail proper nouns, while phrase-like or punctuation-heavy entries are kept weaker.

### Download source dictionary

```powershell
.\tools\dictionary\import_nico_pixiv.ps1
```

### Convert to Mozc system dictionary delta

```powershell
python tools/dictionary/convert_nico_pixiv.py
```

The generated file is:

```text
src/data/dictionary_koyasi/generated/profiled/dic-nico-pixiv-delta.txt
```

This file is ignored by Git.

### Build daily with Nico/Pixiv delta

The recommended daily workflow already includes nico/pixiv delta conversion:

```powershell
.\tools\dictionary\prepare_daily_dictionary.ps1
```

If source dictionaries are already available locally:

```powershell
.\tools\dictionary\prepare_daily_dictionary.ps1 -SkipDownload
```

## Syntax guard dictionary

`koyasi-syntax-guard.txt` is a small generated dictionary used to protect high-impact grammar-like conversion paths from bad segmentation.

It is intentionally conservative. It is not a broad automatic prefix dictionary.

Examples of guarded paths:

```text
とうちたい       と打ちたい
とうちたいのに   と打ちたいのに
にわける         に分ける
のに             のに
```

The syntax guard dictionary is generated by:

```powershell
python tools/dictionary/generate_syntax_guard_dictionary.py
```

The output file is:

```text
src/data/dictionary_koyasi/generated/profiled/koyasi-syntax-guard.txt
```

A debug TSV is also written to:

```text
dist/dictionary/koyasi-syntax-guard-debug.tsv
```

The guard dictionary should remain small. Avoid broad generated prefix entries that produce many unrelated candidates such as `と + 固有名詞`.

Current guard generation has three layers:

1. Collision-driven guards generated from suspicious base-dictionary collision candidates.
2. Fixed function phrase guards such as `のに`, `には`, `では`, `として`, and `について`.
3. A very small set of seeded guards for observed high-impact segmentation failures.

Seeded guards are acceptable only when all of the following are true:

- The bad conversion has been observed in real use.
- The correct conversion is unambiguous.
- The guard is likely to help common daily input.
- The number of generated guard entries remains very small.
- The guard does not broadly suppress normal dictionary behavior.

Do not use the suffix collision scanner output as a demotion list. It is diagnostic only.

## Diagnostic tools

Use the following tool to inspect which dictionary contains a specific entry:

```powershell
python tools/dictionary/find_dictionary_entries.py --key とうちたい
python tools/dictionary/find_dictionary_entries.py --value 統治体
python tools/dictionary/find_dictionary_entries.py --key ままかつ
python tools/dictionary/find_dictionary_entries.py --key にわける
```

Use the following tool to scan possible suffix collision candidates:

```powershell
python tools/dictionary/scan_suffix_collision_candidates.py --max-cost 8500 --limit 120
```

The suffix collision scanner is diagnostic only. Its output should not be blindly demoted because many valid daily words can match the same suffix patterns.

## Build profiles

### `sample`

Tracked, small, and safe for committed Bazel state.

Use this before committing unrelated work:

```powershell
.\tools\dictionary\use_merge_ut_profile.ps1 -Profile sample
```

### `daily`

Locally generated enhanced dictionary for daily use.

It currently combines:

- merge-ut daily profile
- nico/pixiv delta dictionary
- syntax guard dictionary

Prepare it with:

```powershell
.\tools\dictionary\prepare_daily_dictionary.ps1
```

Or, when sources are already available:

```powershell
.\tools\dictionary\prepare_daily_dictionary.ps1 -SkipDownload
```

### `rich`

Recall-oriented profile generated from a larger merge-ut source set. This is useful for experiments but is not the default daily profile.

## Commit policy

Commit scripts, BUILD files, documentation, and small tracked samples.

Do not commit large generated dictionaries under:

```text
src/data/dictionary_koyasi/generated/
dist/
```

Before committing, verify:

```powershell
.\tools\dictionary\use_merge_ut_profile.ps1 -Profile sample
git status --short
```

`src/data/dictionary_oss/BUILD.bazel` should not appear unless the intended committed state has changed.
