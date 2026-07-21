# Rule-based raw typing correction

Mozkey's raw-input correction is deterministic and rule-based. It uses no ML
model, external service, telemetry, or external corpus. Production hypotheses
are bounded to one edit and are replayed through the Composer/Table state that
created the source composition.

## Pipeline

1. M0 stores authored Roman and JIS-kana Gold/Negative fixtures. Schema v3 is
   the single contract: the validator, corpus generator, report, Python tests,
   and generated C++ headers derive columns, enums, counts, strata, runtime
   limits, and release thresholds from `corpus_schema.json`. The generator
   validates UTF-8/IDs/readings and emits deterministic production rules with
   a corpus version and SHA-256. Independent Roman/kana holdout rows are
   compiled into test fixtures but are not inputs to rule generation.
2. M1 retrieves the source raw sequence and replays Roman candidates through
   the original Composer/Table. M6 does the same with the physical JIS
   `key_code` plus `key_string` trace, including modifier-sensitive events.
3. M2 enumerates corpus, transpose, neighbor, duplicate, and modifier
   hypotheses, deduplicates them, and selects a priority-queue top-K bounded
   set. Generic rules are candidate-only.
4. M3 shadow-converts each replayed reading with the normal converter request,
   folds its segments into one whole-sequence candidate, and ranks converter
   cost plus edit cost. `src/typing_correction:inspect_typing_correction`
   prints the raw/replay boundary; `benchmark_typing_correction` reports raw
   enumeration timings.
5. M4 appends at most two `TYPING_CORRECTION` candidates to the original
   candidate window for conversion, suggestion, and prediction. Preview,
   result tokens, consumed source length, commit, and external corrected
   reading learning are handled without replacing the source Segments.
6. M5 applies a unique high-confidence alternative to live conversion only
   when it beats the source candidate after edit cost and is not shadowed by
   an exact user/project dictionary entry. Source raw/key state remains the
   authority for pending suffixes and Esc recovery; corrected raw/reading is a
   separate display/Zenz/commit state.
7. M7 adds a bounded session-local prefix cache, stale-timestamp rejection for
   local accept/reject decisions, and a candidate-only kana-as-Roman mode
   mismatch replay. No decision state is persisted.

## Safety boundary

Roman correction requires the feature flag, lower-case ASCII raw input, a tail
cursor, non-secure/non-password input, non-reverse conversion, non-ASCII input
mode, no mixed script, and no URL/email/path-like shape. Raw length is limited
to 4–64 bytes. JIS-kana correction additionally requires a complete physical
key-code/key-string trace, JIS kana preedit mode, a tail cursor, and disabled
kana-modifier-insensitive conversion. Both paths fail closed in secure input.

Explicitly excluded are arbitrary long insertions, two-or-more automatic
edits, grammar/surface correction, external services/telemetry, partial-clause
correction, and Dvorak/Colemak layouts.

## Verification policy

Real-user telemetry validation is intentionally omitted. The quality gate is
test-based and release-scale:

- The generated evaluation corpus contains 1,000 cases: 250 Roman Gold, 250
  Roman Negative, 100 unique multi-key kana Gold, 150 kana Negative, 100 Roman
  Composer/Engine holdout cases, and 150 multi-key kana holdout cases. Gold
  cases cover raw/key replay, reading, operation, and
  automatic-vs-suggestion policy; Negative cases measure candidate and
  automatic false-positive behavior.
  `kana_gold.tsv` is a separate 100-row one-event rule fixture and is not
  counted in the release total.
- The Roman holdout is replayed through the actual Composer and attached by
  the Engine test as a `TYPING_CORRECTION` candidate. Kana holdout traces are
  disjoint from generated inputs by both key code and key string, and cover
  neighbor, modifier, and duplicate operations.
- Negative policy is explicit and split into three independent decisions:
  `raw_policy` controls the raw-correction gate, `display_policy` controls the
  user-visible Engine candidate window, and `auto_policy` controls automatic
  application. Roman has 90 `raw_policy=forbidden` rows for URL, digit, and
  other raw-gate safety cases, plus 40 gate-eligible
  `display_policy=forbidden` normal-word rows. Kana has 75 raw-gate rows and
  75 layout-valid display-suppression rows. Every Negative row has
  `auto_policy=forbidden`; Kana automatic correction is disabled and all Kana
  Gold rows are suggestion-only. Raw-hypothesis FPR uses the raw-policy
  denominator, while Engine candidate-window FPR uses the display-policy
  denominator. Remaining policy-allowed rows are diagnostics, not release
  FPR denominators. Roman `auto_recall` uses only 32 `behavior=auto` Gold
  rows, while `suggest_recall` uses the other 218 Gold rows.
- The metric gate aggregates raw-hypothesis precision, recall, and top-1
  accuracy, per-operation/length/position/lexical/feature metrics, strict
  Negative FPR, and automatic safety. The Engine binary separately reports
  candidate-window top-1, top-K, and strict Negative FPR. Release thresholds
  for these values, plus Composer and Engine E2E recall, are stored in schema
  v3 and applied by `validate_typing_correction_metrics.py`. Strata cover operation,
  length, error position, lexical class, and features such as `n`, sokuon,
  small kana, dakuten, handakuten, proper nouns, technical/product names, and
  URLs.
- Unit tests cover parser/schema errors, invalid UTF-8, contradictory readings,
  custom tables, mixed input/cursor gates, converter cost, whole-sequence
  boundaries, dedup/top-K, JIS neighbors, dakuten/handakuten, small kana,
  mode mismatch, cache prefix pruning, timestamps, and fuzz-like bounded input.
- Engine/Session tests cover candidate-only conversion and suggestion paths,
  live preview, pending source-prefix handling, Esc recovery, Enter commit,
  corrected external learning boundary, and source candidate preservation.
- The enforced limits are 4–64 Roman raw bytes, 64 physical kana key events,
  max edit cost 300, max raw hypotheses 16, max replayed readings 3, max
  candidate-window additions 2, and max edits 1. Secure/password gates
  execute no correction and typo raw readings are never learned.

Run the Python corpus gate from the repository root:

```text
python3 -m unittest discover -s tools/typing_correction -p '*_test.py'
python3 tools/typing_correction/expand_evaluation_corpus.py
python3 tools/typing_correction/build_typing_correction_rules.py
python3 tools/typing_correction/validate_typing_correction_corpus.py
python3 tools/typing_correction/corpus_report.py
```

Build and run the official Engine metrics target after the Python gate:
The Bazel workspace is `src`, so run the following two commands from there
and return to the repository root for the Python checker.

```text
cd src
bazel build //engine:evaluate_typing_correction_metrics \
  -c dbg --define=mozkey_dictionary_profile=release-approved-only
bazel-bin/engine/evaluate_typing_correction_metrics > /tmp/typing-correction-metrics.json
cd ..
python3 tools/typing_correction/validate_typing_correction_metrics.py \
  /tmp/typing-correction-metrics.json
```

The current official report replays 98/100 Composer holdout cases and exposes
93/100 expected candidates through Engine E2E. Engine candidate-window top-1
and top-K are 68/100 and 93/100, and the display-policy Negative FPR is 0/40.
Roman raw-hypothesis candidate precision/top-1 are 0.0625/0.98, with raw FPR,
automatic precision/recall, and suggestion automatic-violation rate all 0.
Kana raw-hypothesis candidate precision/top-1 are 0.077821/0.15; automatic
correction is disabled. The independent 150-case Kana Engine holdout reports
29/150 Engine recall, 12/150 candidate-window top-1, and 29/150 top-K, so its
positive release thresholds currently fail. The Kana Engine candidate-window
Negative FPR is also reported separately as 75/75 (1.0), so the report
correctly fails the zero-FPR release threshold until the runtime candidate
policy is corrected. The release thresholds are stored in schema v3 and applied by
`validate_typing_correction_metrics.py`.
