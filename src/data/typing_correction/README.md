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

`kana_gold.tsv` and `kana_negative.tsv` use physical JIS key codes together
with their key strings. They are replay fixtures for neighbor,
dakuten/handakuten, and small-kana handling; the replay path never
reinterprets a Roman raw string as a kana trace.
`kana_jis_layout.tsv` documents the physical layout used by the bounded
neighbor generator, and `kana_modifier_rules.tsv` documents the explicit
modifier rules. The generated C++ tables are derived from the Gold/Negative
fixtures by the same deterministic rule generator as the Roman tables.

The current generated corpus has 30 Roman Gold cases, 20 Roman Negative
cases, 20 kana Gold cases, and 20 kana Negative cases. `roman_holdout.tsv`
and `kana_holdout.tsv` contain 6 and 4 independent, previously unregistered
cases respectively. Holdout rows are compiled into test fixtures only; they
are excluded from generated production rules and from the production corpus
SHA-256. They exercise generic candidate generation, including multi-key
kana traces, without rewarding a rule table that memorizes the Gold rows.

`corpus_schema.json` is the checked-in column/limit contract and
`PROVENANCE.md` records that the fixtures are project-authored.

CI validates the exact counts and schema, operation coverage, negative
automatic-correction behavior, independent holdout behavior, candidate
limits, replay safety, and generated-header determinism. This is a regression
and coverage gate, not a statistically representative estimate of real-use
accuracy; real-use validation is intentionally out of scope.
