# Provenance

The Roman and JIS-kana rows in this directory are project-authored, synthetic
evaluation fixtures. They are not copied from an external corpus and are not
used to train an ML model. The release gate contains 1,000 cases: 250 Roman
Gold, 250 Roman Negative, 100 kana evaluation Gold, 150 kana Negative, 100 Roman
Composer/Engine holdout cases, and 150 kana holdout cases. Each Gold/Negative
row exists to pin one rule, safety boundary, or negative decision in
deterministic tests. The separate holdout rows are also project-authored and
are reserved for candidate-only runtime tests; they are not generated into
production rules and are excluded from the production corpus digest.
The 100-row `kana_gold.tsv` fixture is intentionally outside the release total
and is used only to generate one-event rules; `kana_evaluation.tsv` contains
unique multi-key traces for release metrics.

`corpus_schema.json` is version v3 and is the single source of the TSV
columns, enum values, 1,000-case targets, required coverage strata, negative
candidate/automatic policies, runtime budgets, exclusivity checks, and release
thresholds. The validator, deterministic expansion script, report, generated
C++ contract, and metric-threshold checker consume those schema values
directly or through generated artifacts.

Negative policy is intentionally split into raw, display, and automatic
decisions. 90 Roman and 75 Kana rows forbid raw hypotheses; 40 Roman and 75
Kana gate-eligible rows forbid visible Engine candidates; the remaining
Negative rows permit diagnostic suggestions. All Negative rows forbid
automatic correction. Kana automatic correction is disabled and every Kana
Gold row is suggestion-only. Roman `auto_recall` uses only Gold rows marked
`behavior=auto`; the separate `suggest_recall` metric covers suggestion Gold
rows. The Engine metric now evaluates both display-policy sets and all 150
Kana holdout positives. The current Kana positive result is 29/150 Engine
recall, 12/150 candidate-window top-1, and 29/150 top-K; its 75/75 display
Negative result is an intentional release-gate failure until the runtime
candidate policy is corrected.

Changes to Gold, Negative, or override rows require regenerated artifacts and
the corresponding unit-test update. Holdout changes require regenerating the
holdout header and updating the independent holdout assertions, but do not
change the production rule digest. Roman generated rules record the SHA-256
of the three Roman TSV files; kana key traces are replayed directly from the
event fields in the fixture.
