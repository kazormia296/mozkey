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
- Only the final publish job has `contents: write`; build jobs are read-only.
- Every external GitHub Action is pinned to a full commit SHA. Dependabot checks
  those pins weekly and proposes reviewed updates.
- The publish job creates GitHub Artifact Attestations for every release asset,
  including `SHA256SUMS`. Verify an asset with
  `gh attestation verify --repo kazormia296/mozkey-ibg <asset>`.
- The workflow creates one draft prerelease. Publishing it remains a deliberate
  human action while the Windows packages are unsigned. The macOS package must
  pass Developer ID signature, notarization-ticket, and Gatekeeper checks before
  it can be attached to the draft.

The expected public artifacts are:

- `Mozkey_vX.Y.Z_x64.msi`
- `Mozkey_vX.Y.Z_universal.msi`
- `Mozkey_vX.Y.Z_arm64.msi`
- `Mozkey_vX.Y.Z_macos_arm64.pkg`
- `mozkey-ibg_X.Y.Z_amd64.deb`, its Ubuntu build attestation and SPDX inventory
- `mozkey-ibg-X.Y.Z-1.x86_64.rpm`, its Fedora build attestation and SPDX inventory
- `mozkey-vX.Y.Z-archlinux-x86_64.tar.xz` and its checksum, build attestation,
  and SPDX inventory
- `ubuntu-build-packages.txt`, `fedora-build-packages.txt`, and
  `archlinux-build-packages.txt`
- `SHA256SUMS`

The checksum file is not the only provenance record: the published assets and
`SHA256SUMS` also have GitHub Artifact Attestations bound to their exact
SHA-256 subjects.

Ubuntu 24.04 builds the amd64 Debian package and verifies its multiarch addon
layout from the fixed Ubuntu snapshot configured by
`tools/release/use_ubuntu_snapshot.sh`. Fedora and Arch use digest-pinned
container images plus fixed Fedora/Arch archive repositories; their resolved
package inventories are published with the release, and the Arch job does not
perform a rolling `pacman -Syu` upgrade. Both native packages contain an
attested, pinned, CPU-only `llama-server`. The Arch payload uses the
distribution `llama-cpp` runtime and is the source for the separately
published `mozkey-ibg-bin` AUR package. AppImage is not a product target
because this Fcitx5 IME must install system addon and input-method metadata.
Android and iOS are outside Grimodex's supported platform scope and are not
product build, CI, or release targets in this fork.

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

The publish job uses GitHub's generated release notes. No external note-generation
Action or API secret is used. The draft records `github-generate-notes` as the
generator so reviewers can verify the source before publishing.

## macOS Developer ID signing

The reusable macOS release workflow builds and probes the unsigned payload
before it loads signing credentials. It then signs nested code with a Developer
ID Application identity, signs the installer with a Developer ID Installer
identity, submits the `.pkg` to Apple's notary service, staples the ticket, and
checks the final package with `pkgutil`, `stapler`, and Gatekeeper.

Configure these Actions secrets in the `mozkey` repository:

- `APPLE_CERTIFICATE`
- `APPLE_CERTIFICATE_PASSWORD`
- `APPLE_INSTALLER_CERTIFICATE`
- `APPLE_INSTALLER_CERTIFICATE_PASSWORD`
- `KEYCHAIN_PASSWORD`
- `APPLE_API_KEY_BASE64`
- `APPLE_API_KEY`
- `APPLE_API_ISSUER`

The application certificate, keychain password, and App Store Connect API key
material can use the same values and secret names as the Grimodex repository.
GitHub repository secrets are not automatically visible to another repository,
so those values still need to be configured for `mozkey`. A `.pkg` additionally
requires the two `APPLE_INSTALLER_CERTIFICATE*` secrets; Grimodex's `.dmg`
release does not use an installer certificate.

## AUR publication

Publishing a reviewed GitHub Release triggers **Publish AUR package**. That
workflow accepts only an exact `vX.Y.Z` published release, verifies the
GitHub digest against the Arch checksum sidecar, renders `PKGBUILD`, regenerates
`.SRCINFO` with Arch `makepkg`, and pushes `mozkey-ibg-bin` to AUR. Draft
releases never reach AUR.

One-time repository setup is required before the first publication:

1. Add a dedicated unencrypted Ed25519 automation public key to the maintainer's
   AUR account.
2. Create the GitHub environment `aur`, preferably with a required reviewer.
3. Store the matching private key as that environment's
   `AUR_SSH_PRIVATE_KEY` secret.

After setup, a manual run with an already-published tag safely retries the first
registration or a later update. See [`packaging/aur/README.md`](../packaging/aur/README.md)
for the exact bootstrap and recovery contract.

## Reruns and recovery

- Rerunning a failed build is safe: workflow artifacts are temporary and the
  workflow mutates assets only while the release is a draft.
- If a draft already exists, the workflow refreshes its notes, deletes every
  existing draft asset, and uploads the complete allowlisted desktop asset set.
  Manual attachments are therefore removed and must be added only after the
  workflow succeeds.
- The workflow rechecks that the release is still a mutable draft immediately
  before destructive asset operations and refuses to turn a published release
  back into a draft.
- The expected filename set is checked before upload, after draft cleanup, and
  again after upload. A stale, missing, or unexpected asset fails publication.
- The workflow refuses to modify an already published release.
- Delete an invalid tag only after confirming that no published release depends
  on it; prefer fixing the version on a new commit and creating a new tag.
