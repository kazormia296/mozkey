# Rule-based raw typing correction

Mozkey's raw-input correction is deterministic and rule-based. It uses no ML
model, external service, telemetry, or external corpus. Production hypotheses
are bounded to one edit and are replayed through the Composer/Table state that
created the source composition.

## Pipeline

1. M0 stores authored Roman and JIS-kana Gold/Negative fixtures, validates
   UTF-8/schema/IDs/readings, and generates deterministic production rules
   with a corpus version and SHA-256. Independent Roman/kana holdout rows are
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

Real-use accuracy validation is intentionally omitted. The quality gate is
test-based:

- The generated corpus contains 30 Roman Gold / 20 Roman Negative and 20 kana
  Gold / 20 kana Negative cases. Gold cases cover raw/key replay, reading,
  operation, and automatic-vs-suggestion policy; Negative cases have zero
  automatic output.
- A disjoint 6-case Roman and 4-case multi-key kana holdout is exercised at
  runtime. It is candidate-only and does not alter the production rule digest,
  so the test can detect generic-operation regressions without turning the
  holdout into a generated rule.
- Unit tests cover parser/schema errors, invalid UTF-8, contradictory readings,
  custom tables, mixed input/cursor gates, converter cost, whole-sequence
  boundaries, dedup/top-K, JIS neighbors, dakuten/handakuten, small kana,
  mode mismatch, cache prefix pruning, timestamps, and fuzz-like bounded input.
- Engine/Session tests cover candidate-only conversion and suggestion paths,
  live preview, pending source-prefix handling, Esc recovery, Enter commit,
  corrected external learning boundary, and source candidate preservation.
- The enforced limits are max raw hypotheses 16, max replayed readings 3,
  max candidate-window additions 2, and max edits 1. Secure/password gates
  execute no correction and typo raw readings are never learned.

Run the Python corpus gate from the repository root:

```text
python3 -m unittest discover -s tools/typing_correction -p '*_test.py'
python3 tools/typing_correction/validate_typing_correction_corpus.py
python3 tools/typing_correction/corpus_report.py
```
