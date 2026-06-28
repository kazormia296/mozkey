# General Vocabulary Evaluation

This directory documents the evaluation strategy and reusable tooling for
Mozkey-specific general vocabulary coverage.

The evaluation is separate from the generated external dictionary workflows
under `src/data/dictionary_koyasi/generated/`.

## Goals

The evaluation has two different goals:

* keep normal Japanese vocabulary stable as natural first candidates
* expose English spelling candidates as secondary candidates without making them
  first candidates

It is also used to detect regressions before adding or publishing dictionary
changes.

## Core policy

Normal Japanese vocabulary and English spelling candidates use different success
criteria.

Normal vocabulary is allowed to become the first candidate.

English spelling candidates are not allowed to become the first candidate. They
are treated as secondary candidates only.

For normal Japanese vocabulary:

```text
missing_or_not_top10 = failure
manual_review_risky = failure
```

For English spelling entries:

```text
ok_top1 = risk / failure
ok_top5 = useful candidate presence
weak_top10 = useful candidate presence
missing_or_not_top10 = acceptable
manual_review_risky = failure
```

## Normal Japanese vocabulary

Normal vocabulary covers entries that are safe and natural as first candidates,
such as:

* daily vocabulary
* practical business vocabulary
* UI / Web / admin-screen vocabulary
* IME / dictionary / conversion vocabulary
* education, medical, EC, payment, delivery, and other practical vocabulary

The normal vocabulary regression should remain clean.

## English spelling entries

English spelling entries are used as secondary candidates for katakana-like
readings.

The target behavior is:

```text
たいぴんぐ -> タイピング first / typing candidate
ふらいんぐ -> フライング first / flying candidate
せってぃんぐす -> セッティングス first / settings candidate
えくすぽーと -> エクスポート first / export candidate
```

English entries are added with a weak secondary-candidate cost:

```text
lid: 1851
rid: 1851
cost: 12000
```

If an English spelling entry becomes the first candidate, remove that English
override instead of making it stronger.

## Files

Product dictionary data:

```text
src/data/dictionary_manual/general_override.txt
src/data/dictionary_manual/BUILD.bazel
src/data_manager/oss/BUILD.bazel
src/data_manager/testing/BUILD.bazel
```

Reusable evaluation tools:

```text
tools/dictionary/evaluate_general_vocab.py
tools/dictionary/summarize_general_vocab_report.py
tools/dictionary/append_general_vocab_rows.py
```

## Validation workflow

Validate an evaluation TSV:

```powershell
python .\tools\dictionary\evaluate_general_vocab.py validate `
  --input <input.tsv>
```

Expected result:

```text
errors: 0
warnings: 0
```

Build evaluation tools and `mozc.data`:

```powershell
cd C:\Users\Makoto\dev\mozc\src

bazelisk --output_user_root=C:/bzl build `
  --config oss_windows `
  --config release_build `
  //converter:quality_regression_main `
  //data_manager/oss:mozc_dataset_for_oss `
  --verbose_failures
```

Run evaluation:

```powershell
cd C:\Users\Makoto\dev\mozc

python .\tools\dictionary\evaluate_general_vocab.py prepare `
  --input <input.tsv> `
  --out-dir <out-dir>

python .\tools\dictionary\evaluate_general_vocab.py run `
  --quality-main .\src\bazel-bin\converter\quality_regression_main.exe `
  --mozc-data .\src\bazel-bin\data_manager\oss\mozc.data `
  --out-dir <out-dir>

python .\tools\dictionary\evaluate_general_vocab.py summarize `
  --input <input.tsv> `
  --out-dir <out-dir>
```

After English spelling changes, verify that `ok_top1` is zero.

After any dictionary change, re-run the normal vocabulary regression.

## Adding entries

For English spelling entries:

* prefer single words over broad generated compounds
* avoid duplicate key/value pairs
* avoid unnatural readings
* use cost `12000`
* remove entries that become first candidates
* treat `ok_top5` and `weak_top10` as useful candidate presence
* treat `missing_or_not_top10` as acceptable

For normal vocabulary entries:

* add only entries that are natural as first candidates
* avoid large mechanical combinations unless they have been reviewed
* require clean regression results

## Packaging check

Before packaging:

```powershell
git diff --check
git status --short
```

CRLF warnings alone are not blocking.

Build package:

```powershell
cd C:\Users\Makoto\dev\mozc\src

bazelisk --output_user_root=C:/bzl build `
  --config oss_windows `
  --config release_build `
  //:package `
  --verbose_failures
```
