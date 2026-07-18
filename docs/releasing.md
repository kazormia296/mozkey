# Releasing Mozkey

Mozkey product builds are release operations. Pull requests and ordinary pushes
run tests, but they do not build or upload installers. A release starts only
from a `vX.Y.Z` tag (or a manual run whose selected ref is such a tag).

## Release contract

- `src/version.bzl` is the source of truth for the user-facing version.
- The tag must be exactly `v<major>.<minor>.<patch>` and must match the three
  `MOZKEY_RELEASE_VERSION_*` values.
- The tagged commit must already be contained in `origin/main`.
- The release workflow reruns the reusable platform tests before it accepts any
  product artifact.
- Only the final publish job has `contents: write`; build and note-generation
  jobs are read-only.
- The workflow creates one draft prerelease. Publishing it remains a deliberate
  human action while the Windows and macOS packages are unsigned.

The expected public artifacts are:

- `Mozkey_vX.Y.Z_x64.msi`
- `Mozkey_vX.Y.Z_universal.msi`
- `Mozkey_vX.Y.Z_arm64.msi`
- `Mozkey_vX.Y.Z_macos_arm64.pkg`
- `mozkey-vX.Y.Z-archlinux-x86_64.tar.xz` and its checksum, build attestation,
  and SPDX inventory
- `mozkey-vX.Y.Z-android-native-libs.zip`
- `SHA256SUMS`

Ubuntu is a compile and multiarch-layout gate, not a published Linux product.
The Arch payload is not a cross-distribution installer or a package repository.

## Prepare and tag a release

1. Update the three `MOZKEY_RELEASE_VERSION_*` values in `src/version.bzl` on a
   release-preparation branch.
2. Merge that branch only after the normal pull-request checks are green.
3. Confirm the intended release commit is on `origin/main` and the tag is new.
4. Create and push an annotated tag:

   ```sh
   git fetch origin main --tags
   git merge-base --is-ancestor <release-commit> origin/main
   git tag -a vX.Y.Z <release-commit> -m "Release vX.Y.Z"
   git push origin vX.Y.Z
   ```

5. Wait for **Mozkey Release** to complete. Open the resulting draft release,
   review every artifact, `SHA256SUMS`, limitations, and release notes, then
   publish it when ready.

The tag/version/ancestry gate runs before any expensive platform build. A bad
tag therefore fails quickly instead of consuming the release matrix.

## Automatic release notes

The release workflow can run the official `openai/codex-action` in a separate,
read-only job. Its prompt scopes the summary to the previous canonical tag, the
current tag, their commit subjects, and their diff. Repository content and
commit messages are treated as untrusted data, and Codex cannot modify the
checkout.

To enable the AI summary, configure the repository Actions secret
`OPENAI_API_KEY`. The key is passed only to the Codex action; it is not exposed
as a job-level environment variable or to build/test steps.

If the secret is absent, Codex fails, or it returns an empty result, publication
does not fail. The workflow falls back to GitHub's generated release notes. The
draft release always records which generator was used so reviewers can verify
the result before publishing.

## Reruns and recovery

- Rerunning a failed build is safe: workflow artifacts are temporary and the
  final asset upload uses replacement semantics only while the release is a
  draft.
- If a draft already exists, the workflow refreshes its notes and replaces
  same-named managed assets. Differently named manual attachments are preserved.
- The workflow refuses to modify an already published release.
- Delete an invalid tag only after confirming that no published release depends
  on it; prefer fixing the version on a new commit and creating a new tag.
