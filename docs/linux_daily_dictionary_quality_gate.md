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
  --evidence-id base-mozc-REVISION \
  --output dist/quality/base_mozc.raw.tsv

python tools/dictionary/fixed_corpus_quality_gate.py evaluate \
  --quality-main /absolute/hazkey-oracle/quality_regression_main \
  --corpus src/data/test/quality_regression_test/regression.tsv \
  --data-file /absolute/hazkey-oracle/mozc.data \
  --evidence-id hazkey-oracle-REVISION \
  --output dist/quality/hazkey.raw.tsv

python tools/dictionary/fixed_corpus_quality_gate.py evaluate \
  --quality-main src/bazel-bin/converter/quality_regression_main \
  --corpus src/data/test/quality_regression_test/regression.tsv \
  --data-file src/bazel-bin/data_manager/oss/mozc.data \
  --evidence-id mozkey-REVISION \
  --output dist/quality/mozkey.raw.tsv
```

`evaluate` appends the SHA-256 of the evaluator executable and `mozc.data` to
the supplied evidence id.  It also accepts the tracked Hazkey AB corpus form
whose header is `id`, `reading`, `expected`, `category`; that form is converted
to a temporary Mozc quality-regression corpus without changing the source
fixture.  Pipe-separated expected alternatives are evaluated as alternatives,
not as one literal surface.

If base Mozc or Hazkey uses the frozen Hazkey ABProbe v3 runner, normalize each
independently executed JSONL artifact.  The adapter verifies the AB schema,
backend, full corpus digest, row order, top-k, warmups, and iterations before
emitting the common format:

```bash
python tools/dictionary/fixed_corpus_quality_gate.py normalize-ab-probe \
  --corpus /absolute/frozen/formal-corpus.tsv \
  --results /absolute/base-mozc-abprobe.jsonl \
  --output dist/quality/base_mozc.raw.tsv \
  --evidence-id base-mozc-B0 \
  --expected-converter-backend mozc \
  --expected-backend B0

python tools/dictionary/fixed_corpus_quality_gate.py normalize-ab-probe \
  --corpus /absolute/frozen/formal-corpus.tsv \
  --results /absolute/hazkey-abprobe.jsonl \
  --output dist/quality/hazkey.raw.tsv \
  --evidence-id hazkey-H0 \
  --expected-converter-backend hazkey \
  --expected-backend Hazkey
```

The normalized rows are:

```text
OK:<TAB>key<TAB>actual<TAB>command<TAB>expected<TAB>evidence-id
FAILED:<TAB>key<TAB>actual<TAB>command<TAB>expected<TAB>evidence-id
```

The number and order of rows, `key`, `command`, and `expected` must match the
fixed corpus.  The gate rejects missing, reordered, or mismatched rows and
writes `decision: invalid_input` plus the error to `summary.json` when an
output directory is available.

The three result paths, full-file SHA-256 values, and per-row evidence ids must
also be distinct.  Reusing one raw TSV for base Mozc, Hazkey, and Mozkey is
invalid input even when it is copied to different path names.  This makes the
gate an independent three-engine comparison rather than a schema-only smoke.

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
This strict union is intentionally conservative: it asks Mozkey to retain the
unique successes of both engines, so it is stronger than either pairwise
comparison.  A diagnostic corpus can therefore have union regressions even
when Hazkey and Mozkey have no statistically significant paired difference.

`summary.json` also reports exact two-sided McNemar diagnostics for
base-Mozc/Hazkey, base-Mozc/Mozkey, and Hazkey/Mozkey.  When the corpus has a
`protected` category, it records both all cases and an
`excluding_protected` scope.  The latter keeps literal-span/session behavior
out of the dictionary-quality comparison; the omitted cases remain visible in
the category counts and require a separate UX/integration gate.  A McNemar
`p >= 0.05` means the artifact did not show a significant paired difference;
it is not proof of equivalence or non-inferiority.

For the Hazkey-to-Mozkey migration decision, the tracked 15-case corpus is:

```text
tools/dictionary/testdata/migration_ab15/corpus.tsv
```

It is a focused decision-spike gate, not a broad language benchmark.  Keep a
larger exploratory corpus as a separate diagnostic: a failure on that corpus
must be reported and investigated, but must not be relabelled as a passing
15-case migration gate.  Conversely, the 15-case pass must not be described as
proof that Mozkey preserves every Hazkey conversion.  For the 2026-07-17
migration decision, the 15-case strict-union result is the hard gate; the
1,360-case frozen corpus is a paired diagnostic and its strict-union exit code
is not a release blocker by itself.  See
`docs/linux_mozkey_migration_quality_evidence_20260717.md` for the sealed
artifacts, statistical result, protected-surface split, and repair bound.

Run the bundled gate fixture without downloads or a Bazel build:

```bash
python tools/dictionary/fixed_corpus_quality_gate.py smoke \
  --out-dir dist/quality/fixed-corpus-smoke
```

## Artifact schema

`dist/quality/fixed-corpus/comparison.tsv` uses schema
`mozkey.fixed_corpus_gate.v2` and has one ordered row per corpus case:

| Field group | Fields |
| --- | --- |
| Identity | `case_index`, `case_id`, `category`, `key`, `expected`, `mode` |
| Engine observations | `{base_mozc,hazkey,mozkey}_status`, `{base_mozc,hazkey,mozkey}_output` |
| Decision | `oracle_required`, `classification` |

`classification` is one of `kept_oracle_case`, `regression`,
`mozkey_improvement`, or `all_failed`.

`dist/quality/fixed-corpus/summary.json` contains:

- `schema_version`, `decision`, and the exact policy;
- corpus path, SHA-256, and case count;
- each engine result path, SHA-256, passed count, and failed count;
- each engine's unique evidence id, including executable/data or AB artifact
  identity;
- oracle-required, regression, improvement, and all-failed counts;
- the same counts and per-engine pass totals by category;
- exact paired McNemar counts and p-values for all cases and, when present,
  the scope excluding `protected` cases;
- the comparison TSV path.

Both output files are generated under ignored `dist/`; commit the tooling,
fixtures, and documentation only.

Linux CI uploads `dist/quality/**` and the daily source manifest with
`if: !cancelled()`, so a regression decision or preparation failure retains
the available comparison, digest, and status diagnostics.
