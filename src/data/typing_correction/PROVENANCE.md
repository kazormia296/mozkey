# Provenance

The Roman and JIS-kana rows in this directory are project-authored, synthetic
evaluation fixtures. They are not copied from an external corpus and are not
used to train an ML model. Each Gold/Negative row exists to pin one rule,
safety boundary, or negative decision in deterministic tests. The separate
holdout rows are also project-authored and are reserved for candidate-only
runtime tests; they are not generated into production rules and are excluded
from the production corpus digest.

Changes to Gold, Negative, or override rows require regenerated artifacts and
the corresponding unit-test update. Holdout changes require regenerating the
holdout header and updating the independent holdout assertions, but do not
change the production rule digest. Roman generated rules record the SHA-256
of the three Roman TSV files; kana key traces are replayed directly from the
event fields in the fixture.
