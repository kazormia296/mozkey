# Rule-based raw typing-correction corpus

This directory contains Mozkey's rule-based Roman raw-input correction
corpus. It is evaluation data and rule-generation input, not a
machine-learning training set.

The files are authored for this project. No external corpus is copied into
the repository. The deterministic generator records the corpus version and
SHA-256 in the generated C++ rule table.

`roman_gold.tsv` contains intended raw input, a representative mistyped raw
input, the expected reading, and the expected policy. `roman_negative.tsv`
contains inputs for which automatic correction must not be selected.
`roman_rule_overrides.tsv` contains deliberately conservative local rules
that are useful for candidate generation but are not automatically applied.

`kana_gold.tsv` is the one-event rule-generation fixture. `kana_evaluation.tsv`
is the release-facing 100-case multi-key evaluation set; its typed traces are
unique so repeated rule fixtures do not become repeated evaluation inputs.
Both use physical JIS key codes together with their key strings. The replay
path never reinterprets a Roman raw string as a kana trace.
`kana_jis_layout.tsv` documents the physical layout used by the bounded
neighbor generator, and `kana_modifier_rules.tsv` documents the explicit
modifier rules. The generated C++ tables are derived from the Gold/Negative
fixtures by the same deterministic rule generator as the Roman tables.

The release-scale evaluation corpus has 1,000 cases: 250 Roman Gold, 250
Roman Negative, 100 kana evaluation Gold, 150 kana Negative, 100 independent Roman
Composer/Engine holdout cases, and 150 independent multi-key kana holdout
cases. The separate 100-row kana rule fixture is outside that total and is used
only to generate production rules. Kana evaluation rows explicitly cover
physical neighbors, small kana, dakuten, and handakuten. Holdout rows are compiled into test fixtures only;
they are excluded from generated production rules and from the production
SHA-256. A kana holdout is disjoint from generated inputs by both its physical
key-code trace and its key-string trace.

`corpus_schema.json` v3 is the single contract for these files. The generator,
validator, corpus report, Python tests, and generated C++ headers derive their
columns, enums, target counts, strata, runtime limits, and release thresholds
from it; generated headers must be regenerated rather than edited directly.

Negative rows separate `raw_policy`, `display_policy`, and `auto_policy`.
Roman has 90 `raw_policy=forbidden` rows for raw-gate safety and 40
gate-eligible `display_policy=forbidden` normal-word rows. Kana has 75
raw-gate rows and 75 layout-valid display-suppression rows. Every Negative row
has `auto_policy=forbidden`. Raw-hypothesis false-positive rate uses the
raw-policy denominator; Engine candidate-window false-positive rate uses the
display-policy denominator. Policy-allowed rows remain diagnostics, and
`negative_candidate_rate` is an all-Negative diagnostic. Roman `auto_recall`
is therefore over the 32 `behavior=auto` Gold rows and `suggest_recall` is over
the 218 suggestion Gold rows, not over all 250 Gold rows.

The metric gate reports raw-hypothesis precision/recall/top-1, per-operation
and per-stratum metrics, strict Negative FPR, and separate Engine candidate-
window top-1/top-K/FPR. Kana automatic correction is disabled; all Kana Gold
rows are suggestion-only and the gate requires zero automatic hypotheses.
Composer replay is currently 98/100 and the official Engine metrics target is
currently 93/100 (Engine candidate-window top-1/top-K are 68/100 and 93/100;
Roman display-policy Negative FPR is 0/40). The independent 150-case Kana
Engine holdout currently reports 29/150 recall, 12/150 candidate-window
top-1, and 29/150 top-K; the corresponding positive thresholds are enforced
and currently fail. The newly enforced Kana candidate-window Negative FPR also
reports 75/75 and blocks the release gate. The schema thresholds are 0.95
Composer recall, 0.90 Roman Engine recall, 0.90 Kana Engine recall, 0.50
Kana Engine top-1, 0.90 Kana Engine top-K, and zero display FPR.
The Engine result is emitted by
`//engine:evaluate_typing_correction_metrics`; the typing-correction-only
binary leaves `engine_e2e_recall` null to avoid an engine dependency cycle.
Counts and schema-derived strata are also emitted by
`tools/typing_correction/corpus_report.py`.

Roman length strata mean raw ASCII character count; kana length strata mean
physical key-event count. The runtime contract is min/max Roman raw bytes
4/64, max kana key events 64, max edit cost 300, max raw hypotheses 16, max
replayed readings 3, max edits 1, and max candidate-window additions 2.

`PROVENANCE.md` records that the fixtures are project-authored.

CI validates the exact 1,000-case counts and schema, operation/stratum
coverage, negative automatic-correction behavior, independent holdout
behavior, candidate limits, replay safety, and generated-header determinism.
The official metrics command applies the aggregate release thresholds. This is
a deterministic synthetic regression gate, not a statistically representative
estimate of real-use accuracy; real user telemetry remains out of scope.
