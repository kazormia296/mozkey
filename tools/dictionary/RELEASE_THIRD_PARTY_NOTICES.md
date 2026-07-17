# Mozkey Release Dictionary Notices

The public `release-approved-only` system dictionary is generated from the
exact revisions and payload digests recorded in `daily_sources.lock.json`.
That lock file is installed next to this notice. The CI dictionary transfer
artifact contains only the three generated files listed by
`linux-release-approved-output-manifest.json`. The final binary product embeds
the compiled system dictionary; neither it nor the CI transfer artifact ships
the raw downloaded source data.

## merge-ut-dictionaries

* Revision: `15c1c64502b43e31d328012860376c03c3eaf633`
* Source: <https://github.com/utuhiro78/merge-ut-dictionaries>
* Use: deterministic merge, Mozc ID update, duplicate removal, and cost
  adjustment for the approved sources below
* Source-code license: Apache License, Version 2.0

Upstream describes the general `mozcdic-ut.txt` output as `Combined`. Mozkey's
public release profile enables only the pinned place-name and SudachiDict
inputs below; broader merge-ut profiles remain local experiments and are not
release inputs.

## mozcdic-ut-place-names

* Revision: `9951ef87972c1d2c3803e2106b86083d14a242f9`
* Payload SHA-256:
  `fc27d40f32e99f18849730cdc0b14ddcf1dd6c9ce49b4c3ac65a0b17e6499fce`
* Source: <https://github.com/utuhiro78/mozcdic-ut-place-names>
* Upstream data notice: Japan Post ZIP-code data; upstream states that Japan
  Post does not claim copyright in the ZIP-code data (public domain)

## mozcdic-ut-sudachidict

* Revision: `7def3da408b1854801bd5b559273f9fb8001ef5b`
* Payload SHA-256:
  `696e745b7f7e9e202497421cee048de058d85b79cc79edc31c4f89b7b33675d9`
* Source: <https://github.com/utuhiro78/mozcdic-ut-sudachidict>
* License: Apache License, Version 2.0

## mozcdic-ut-personal-names

* Revision: `159e01bf4af61a3fa9367ae2578fd42c464a0ea7`
* Compressed payload SHA-256:
  `e7a017a3422fabdae9c881a422c1825416d1d96531b05eca7b2a878058a48751`
* Source: <https://github.com/utuhiro78/mozcdic-ut-personal-names>
* License: Apache License, Version 2.0

The complete Apache License, Version 2.0 text is installed as
`Apache-2.0.txt` in this directory.

## Explicitly excluded from public artifacts

`dic-nico-intersection-pixiv` is pinned for repeatable local quality
evaluation, but its dictionary data is derived from external web sources and
its redistribution conditions are not explicit enough for this release. It is
excluded from the public generation profile, the CI transfer artifact, the
Bazel release dictionary target, and the installed product. It remains
available only through the explicitly named `local-evaluation` profile.
