# Mozkey migration A/B corpus

This is the tracked copy of Hazkey's `ime-base-ab-v1` decision-spike corpus.
Its UTF-8 bytes have SHA-256
`e5c61cc92042c24ff334f702c7bd3e01473e37002c9d64d6c652462721520e9e`.

Use it only for the migration non-regression gate described in
`docs/linux_daily_dictionary_quality_gate.md`.  It is deliberately small and
is not a comprehensive Japanese language benchmark.  Base Mozc and Hazkey
must still be executed independently; copying one result TSV into all three
engine inputs is invalid evidence.
