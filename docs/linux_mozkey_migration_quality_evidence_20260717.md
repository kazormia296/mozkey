# Mozkey migration quality evidence (2026-07-17)

## Disposition

The focused 15-case migration gate passes.  The broader 1,360-case corpus does
not show a statistically significant Hazkey advantage after the 100
mixed-literal `protected` cases are separated from dictionary quality.  This
supports continuing the Mozkey migration rather than extending the Hazkey
engine-selection spike.

The broad strict-union result still fails and is not hidden: Mozkey does not
reproduce every case uniquely passed by Hazkey.  It is a diagnostic backlog,
not the 15-case release gate.  The protected-surface result needs a separate
product UX/integration gate before the migration is called complete.

## Sealed inputs

| Artifact | SHA-256 / identity |
| --- | --- |
| Focused 15-case corpus | `e5c61cc92042c24ff334f702c7bd3e01473e37002c9d64d6c652462721520e9e` |
| Frozen 1,360-case corpus | `cdb2a017b4548f6f77ec3d466f84ec09268a74adb5e876e224e01069f128c8ae` |
| Frozen B0 ABProbe v3 JSONL | `08600f8f7367535469a3f383343dc521181faf8853b5a913336afd33104feea7` |
| Frozen Hazkey H0 ABProbe v3 JSONL | `29a8b543b86b1e596774f0fac6d1c836a9a0b7a2d60d5e782c91b374fe34b4b6` |
| Current Mozkey ABProbe v3 JSONL | `590e92b0702eb99f29c94bcf044a310a34472b7b67cf1ae941d474576c6fce05` |
| Focused current Mozkey ABProbe v3 JSONL | `17c1aa7ee7202bf1517e301bff9ce17753bf2cee1c2baad2219bfd4ff9bcd6df` |
| Current Mozkey source | `4467cc10c9db3e07635779b26bc4350f7e51159f` |
| Current daily `mozc.data` | `045724831a736cb7d5e58b91db618c71776f37b3ad3b2e6db2d327128350f435`, 36,318,484 bytes |
| Current fixed-boundary helper | `c1c93793243dd210918f3a749feaed855f2e01b63f28f6d857a06adbdf90de97`, 27,352,704 bytes |
| Current evaluation runner | `9501daa3a62cd1093c41bfef818cab917b6fc2b44f58f7ab06543e4b5e7c3ff2`, 35,594,352 bytes |

The current runner was built in a detached Hazkey worktree from `0db223f`.
Only that evaluation worktree was changed: it trusted the current helper/data
pair above and passed the current data SHA to `MozcSidecarClient`.  Frozen
B0/H0 artifacts and their trusted profiles were not modified.

## Focused hard gate

The tracked byte-identical copy is
`tools/dictionary/testdata/migration_ab15/corpus.tsv`.

| Engine | Top-1 accepted | Evidence |
| --- | ---: | --- |
| Base Mozc B0 | 12 / 15 | ABProbe raw `654704a8a0401b3cfa87b169d641038f27ce63cee93d5a3d667e0d119b4ed98b` |
| Hazkey H0 | 10 / 15 | ABProbe raw `6aab0559b99aa99c99cf0e2027d7ffee15e615592189f8b59ec725d7db60ea1e` |
| Mozkey daily | 13 / 15 | ABProbe raw `17c1aa7ee7202bf1517e301bff9ce17753bf2cee1c2baad2219bfd4ff9bcd6df` |

These three observations use the same ABProbe v3 adapter/session semantics.
The strict union has 12 required cases, zero regressions, one Mozkey
improvement (`肌身離さず`), and two cases failed by all engines.  Decision:
`pass`.

## Broad paired diagnostic

All three runs use the frozen corpus, top-10 collection, top-1 evaluation,
zero warmups, one measured iteration, and the ABProbe v3 full-composition
fixed-boundary adapter semantics.

| Scope / engine | Passed |
| --- | ---: |
| All 1,360: Base Mozc B0 | 809 |
| All 1,360: Hazkey H0 | 909 |
| All 1,360: Mozkey daily | 824 |
| Excluding `protected` (1,260): Base Mozc B0 | 799 |
| Excluding `protected` (1,260): Hazkey H0 | 822 |
| Excluding `protected` (1,260): Mozkey daily | 814 |

The all-case strict union contains 1,043 required cases and 226 Mozkey
regressions.  Of those, 224 are Hazkey-only oracle cases; only two were passed
by B0 (one B0-only and one passed by both oracles).  Excluding `protected`
leaves 954 union-required cases, 147 regressions, seven improvements, and 299
cases failed by all three.

Per-category non-protected union regressions are:

| Category | Regressions |
| --- | ---: |
| `technical-mixed` | 30 |
| `proper-noun` | 12 |
| `colloquial` | 21 |
| `homophone-context` | 17 |
| `long-structural` | 27 |
| `grimodex-regression` | 40 |

Exact two-sided McNemar diagnostics for the 1,260-case scope are:

| Pair (left vs right) | Left-only | Right-only | Right - left | p-value |
| --- | ---: | ---: | ---: | ---: |
| Hazkey vs Mozkey | 146 | 138 | -0.635 percentage points | 0.677943 |
| B0 vs Mozkey | 2 | 17 | +1.190 percentage points | 0.000729 |
| B0 vs Hazkey | 132 | 155 | +1.825 percentage points | 0.193986 |

At alpha 0.05, this artifact has no significant Hazkey/Mozkey paired
difference, while Mozkey is significantly better than B0.  The non-significant
Hazkey/Mozkey result is not an equivalence or non-inferiority proof; it is the
correct evidence level for deciding whether the engine-selection spike has
found a meaningful Hazkey advantage.

## Protected-surface split

The 100 `protected` cases contain mixed literals such as paths, URLs, shell
commands, hashes, and structured snippets followed by Japanese readings.
Hazkey passes 87, B0 and Mozkey each pass 10.  Hazkey vs Mozkey consists of 79
Hazkey-only, two Mozkey-only, eight both-pass, and 11 both-fail cases.

This is not a daily-dictionary comparison.  Both adapters use
`HazkeyCompositionSurfaceMapper`, but the ABProbe Mozc path asks the sidecar to
resize one segment across the entire mixed reading and returns candidates from
that forced first segment.  `ProtectedSurfacePolicy` rejects candidates that
damage literal spans; it does not split literals from kana, convert the kana
suffix, and stitch the surface back together.  The common Mozkey failure shape
therefore preserves the literal correctly but leaves the Japanese suffix in
hiragana.

The result is an adapter/segmentation gap under full-composition ABProbe
semantics.  It is not safe to dismiss it as an already-covered session policy,
and it is not evidence for replacing Mozc with Hazkey.  Validate it separately
through the real reducer and Fcitx session with these assertions:

1. literal spans are byte-preserved;
2. adjacent kana spans are converted;
3. segment resize and commit stitch the complete surface correctly;
4. cursor movement and reconversion cannot mutate a protected literal.

## Data-only repair bound

As a diagnostic upper bound, the 147 non-protected union regressions were
converted to exact full-reading dictionary entries using POS IDs 1851/1851 and
cost 100.  The temporary entry file was 21,163 bytes (SHA-256
`14dd39cb80787ac57017e96fa9bb5b1171c9f9db65c61335afa2768646faaca5`).
It produced a 36,335,368-byte data file
(`877f6f10d3f82e73c6005450f0d28598537a5f71c09a38099302a8dcc414cd9c`)
and was evaluated with helper
`646b6c314eaaf61c379f35c1b2a5364b8928e3de40a06e739545e120ca43b6c7`.
The ABProbe raw result is
`9e7b94bc01be121414eab9f5cb1c6350a3a25f6a76b9daafd1c8fca3cf2d039a`.

That data-only experiment reduced the 1,260-case strict-union regressions from
147 to zero without changing the engine or adapter.  Only the 79 protected
regressions remained in the all-case union.  This proves a small, bounded
data-path upper bound; it does not make the exact entries production quality.
They are corpus-derived full-sentence overfit and are deliberately not
committed.  Any product dictionary catch-up must extract reviewable lexical or
domain entries, assign normal costs/POS, and pass a disjoint holdout.

After the experiment, the temporary dictionary source was removed and the
normal daily data was rebuilt byte-identically to
`045724831a736cb7d5e58b91db618c71776f37b3ad3b2e6db2d327128350f435`.

## Reproduction

Prepare and build the current daily artifact:

```bash
python tools/dictionary/prepare_daily_dictionary_linux.py \
  --source-mode cached --pwsh /usr/bin/pwsh --bash-path /bin/bash

cd src
npx --yes @bazel/bazelisk@1.28.1 build --config=oss_linux --jobs=4 \
  //data_manager/oss:mozc.data
cd ..
```

For apples-to-apples acquisition, copy the Hazkey overlay package
`third_party/fcitx-mozkey/overlay/grimodex_mozc_sidecar` into a temporary
package in the Mozkey `src/` workspace, change only its fixed data size/SHA,
and build its helper.  In a detached Hazkey worktree, add only that helper/data
identity to `ABProbeMozcTrustedArtifacts`, pass the same SHA to
`MozcSidecarClient`, and build the evaluation runner.  Package the helper,
data, and v1 manifest as modes 0555, 0444, and 0444 in a 0755
`sha256-<64>` directory.  Do not modify a frozen B0/H0 bundle or runner.

Run the current probe with the same acquisition parameters:

```bash
EVAL_SERVER=/absolute/detached-hazkey-runner
FORMAL_CORPUS=/absolute/frozen/formal-corpus.tsv
CURRENT_BUNDLE=/absolute/sha256-current-bundle

env PATH=/bin:/usr/bin LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC \
  LD_LIBRARY_PATH=/absolute/hazkey/build-grimodex/bin \
  GGML_BACKEND_DIR=/absolute/hazkey/build-grimodex/bin \
  "$EVAL_SERVER" --ab-probe --corpus "$FORMAL_CORPUS" \
  --source-ref 4467cc10c9db3e07635779b26bc4350f7e51159f \
  --warmups 0 --iterations 1 --top-k 10 \
  --backend-name MozkeyDaily --converter-backend mozc \
  --mozc-bundle "$CURRENT_BUNDLE" > MozkeyDaily.jsonl
```

Normalize each independently executed artifact and run the gate:

```bash
python tools/dictionary/fixed_corpus_quality_gate.py normalize-ab-probe \
  --corpus "$FORMAL_CORPUS" --results MozkeyDaily.jsonl \
  --output dist/quality/mozkey-daily.tsv \
  --evidence-id mozkey-daily-4467cc10c \
  --expected-converter-backend mozc --expected-backend MozkeyDaily

python tools/dictionary/fixed_corpus_quality_gate.py gate \
  --corpus "$FORMAL_CORPUS" \
  --base-mozc-result dist/quality/base-mozc.tsv \
  --hazkey-result dist/quality/hazkey.tsv \
  --mozkey-result dist/quality/mozkey-daily.tsv \
  --out-dir dist/quality/formal-migration-diagnostic
```

The command exits 1 for strict-union regressions while retaining
`comparison.tsv`, category counts, and paired diagnostics in `summary.json`.
For this frozen 1,360-case corpus, treat that exit as diagnostic evidence; the
focused 15-case command remains the migration hard gate.
