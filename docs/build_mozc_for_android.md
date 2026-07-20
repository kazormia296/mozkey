# Android source status

Mozkey IbG does not provide an Android product build. Grimodex itself does not
support Android, so this fork has no Android CI workflow, top-level `//:package`
mapping, release job, or public Android artifact. iOS is excluded from the same
product surfaces.

The inherited `src/android/` and `src/ios/` implementation remains in the
repository to keep the Mozkey / Mozc lineage and make upstream maintenance
practical. Its presence does not imply that either mobile platform is supported,
tested, or distributed by Mozkey IbG.

For upstream Mozc's Android library documentation, consult the matching source
revision in the [google/mozc repository](https://github.com/google/mozc). Those
instructions describe upstream Mozc, not a supported build path for this fork.
