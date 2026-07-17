# Linux daily dictionary and fixed-corpus quality gate

This is the reproducible Linux entry point for Mozkey's existing daily
dictionary innovations.  It does not reimplement the merge-ut, Nico/Pixiv,
personal-name, syntax-guard, or profile-selection logic.  Production modes
invoke `tools/dictionary/prepare_daily_dictionary.ps1`; the existing Python
helpers remain the source of truth.

## Daily dictionary source modes

Run these commands from the repository root.

Network-free sample smoke (does not select or retain a daily profile):

```bash
python tools/dictionary/prepare_daily_dictionary_linux.py --source-mode sample
```

Refresh all external inputs, generate the daily profile, and select it:

```bash
python tools/dictionary/prepare_daily_dictionary_linux.py --source-mode download
```

Use repository-local cached inputs without a network fallback:

```bash
python tools/dictionary/prepare_daily_dictionary_linux.py --source-mode cached
```

`download` and `cached` require `pwsh` in `PATH`.  An explicit executable and
bash can be supplied when needed:

```bash
python tools/dictionary/prepare_daily_dictionary_linux.py \
  --source-mode cached \
  --pwsh /usr/bin/pwsh \
  --bash-path /bin/bash
```

Cached mode forwards `-SkipDownload` and fails before running the pipeline if
any of these inputs are absent:

```text
src/data/dictionary_koyasi/generated/mozcdic-ut-safe.txt
src/data/dictionary_koyasi/generated/nico_pixiv/dic-nico-intersection-pixiv-google.txt
src/data/dictionary_koyasi/generated/personal_names/mozcdic-ut-personal-names.txt
```

It never falls back to the network.  `sample` mode instead profiles and checks
the committed 5,000-line merge-ut sample in a temporary directory.  It is a
tooling smoke test, not a daily release artifact.

Production modes write
`dist/dictionary/linux-daily-source-manifest.json`.  Its schema is
`mozkey.daily_dictionary_sources.v1` and records `source_mode`, pipeline
`status`, and the repository-relative path, presence, byte size, and SHA-256 of
each raw input above.  Cached mode fingerprints all three inputs before and
after the PowerShell pipeline and fails if any digest changes during the run.
On pipeline failure, the manifest is rewritten with `status: failed` for CI
diagnostics.

Daily files under `src/data/dictionary_koyasi/generated/` and `dist/` are
ignored and must not be committed.  Daily preparation intentionally selects a
local target in `src/data/dictionary_oss/BUILD.bazel`.  Restore the committed
sample target before committing unrelated work:

```bash
pwsh -NoProfile -File tools/dictionary/use_merge_ut_profile.ps1 -Profile sample
git status --short
```

## Fixed corpus evaluation

Build the existing evaluator and Mozkey data from `src/`:

```bash
cd src
npx --yes @bazel/bazelisk@1.28.1 build \
  --config=oss_linux \
  --spawn_strategy=local \
  //converter:quality_regression_main \
  //data_manager/oss:mozc.data
cd ..
```

Run the same fixed corpus for each engine/data snapshot.  The example below
uses the tracked conversion regression corpus; use absolute paths when results
come from separate Mozc, Hazkey, and Mozkey checkouts.

```bash
python tools/dictionary/fixed_corpus_quality_gate.py evaluate \
  --quality-main /absolute/base-mozc/src/bazel-bin/converter/quality_regression_main \
  --corpus src/data/test/quality_regression_test/regression.tsv \
  --data-file /absolute/base-mozc/src/bazel-bin/data_manager/oss/mozc.data \
  --output dist/quality/base_mozc.raw.tsv

python tools/dictionary/fixed_corpus_quality_gate.py evaluate \
  --quality-main /absolute/hazkey-oracle/quality_regression_main \
  --corpus src/data/test/quality_regression_test/regression.tsv \
  --data-file /absolute/hazkey-oracle/mozc.data \
  --output dist/quality/hazkey.raw.tsv

python tools/dictionary/fixed_corpus_quality_gate.py evaluate \
  --quality-main src/bazel-bin/converter/quality_regression_main \
  --corpus src/data/test/quality_regression_test/regression.tsv \
  --data-file src/bazel-bin/data_manager/oss/mozc.data \
  --output dist/quality/mozkey.raw.tsv
```

If the Hazkey oracle uses its sidecar runner instead of
`quality_regression_main`, its adapter must emit the same ordered raw rows:

```text
OK:<TAB>key<TAB>actual<TAB>command<TAB>expected<TAB>version-or-empty
FAILED:<TAB>key<TAB>actual<TAB>command<TAB>expected<TAB>version-or-empty
```

The number and order of rows, `key`, `command`, and `expected` must match the
fixed corpus.  The gate rejects missing, reordered, or mismatched rows and
writes `decision: invalid_input` plus the error to `summary.json` when an
output directory is available.

Compare all three results:

```bash
python tools/dictionary/fixed_corpus_quality_gate.py gate \
  --corpus src/data/test/quality_regression_test/regression.tsv \
  --base-mozc-result dist/quality/base_mozc.raw.tsv \
  --hazkey-result dist/quality/hazkey.raw.tsv \
  --mozkey-result dist/quality/mozkey.raw.tsv \
  --out-dir dist/quality/fixed-corpus
```

The non-degradation policy is per case, not an aggregate score: every case
passed by base Mozc **or** Hazkey must also pass Mozkey.  A case failed by both
oracles is not required, but a Mozkey pass is recorded as an improvement.

Run the bundled gate fixture without downloads or a Bazel build:

```bash
python tools/dictionary/fixed_corpus_quality_gate.py smoke \
  --out-dir dist/quality/fixed-corpus-smoke
```

## Artifact schema

`dist/quality/fixed-corpus/comparison.tsv` uses schema
`mozkey.fixed_corpus_gate.v1` and has one ordered row per corpus case:

| Field group | Fields |
| --- | --- |
| Identity | `case_index`, `case_id`, `key`, `expected`, `mode` |
| Engine observations | `{base_mozc,hazkey,mozkey}_status`, `{base_mozc,hazkey,mozkey}_output` |
| Decision | `oracle_required`, `classification` |

`classification` is one of `kept_oracle_case`, `regression`,
`mozkey_improvement`, or `all_failed`.

`dist/quality/fixed-corpus/summary.json` contains:

- `schema_version`, `decision`, and the exact policy;
- corpus path, SHA-256, and case count;
- each engine result path, SHA-256, passed count, and failed count;
- oracle-required, regression, improvement, and all-failed counts;
- the comparison TSV path.

Both output files are generated under ignored `dist/`; commit the tooling,
fixtures, and documentation only.

Linux CI uploads `dist/quality/**` and the daily source manifest with
`if: !cancelled()`, so a regression decision or preparation failure retains
the available comparison, digest, and status diagnostics.
