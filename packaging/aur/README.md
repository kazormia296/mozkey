# AUR package publication

The AUR package base is `mozkey-ibg-bin`. The `-bin` suffix is required
because it installs the verified prebuilt Arch Linux payload from the GitHub
Release rather than compiling Mozkey in `makepkg`.

`.github/workflows/aur.yaml` publishes this directory only after a GitHub
Release becomes public. It reads the version and archive checksum from that
published release, verifies them against GitHub's asset digest, regenerates
`.SRCINFO` with Arch `makepkg`, and
pushes `PKGBUILD`, `.SRCINFO`, and `LICENSE` to the AUR `master` branch.
Publication is serialized for this package. Arch `vercmp` rejects an older
release, and a same-version metadata change is rejected unless a reviewed
release changes the package version or increments `pkgrel`.

## One-time AUR setup

1. Create a dedicated, unencrypted Ed25519 key for AUR automation. Do not
   reuse a personal or passphrase-protected key:

   ```sh
   ssh-keygen -t ed25519 -N '' -C mozkey-aur-ci -f ~/.ssh/mozkey-aur-ci
   ```

2. Add its public key to the maintainer account at
   <https://aur.archlinux.org/account/>.
3. Create the GitHub environment `aur`, preferably with a required reviewer.
4. Add the private key as the `AUR_SSH_PRIVATE_KEY` secret in that environment.
5. Publish a reviewed Mozkey GitHub Release. The first authenticated push
   creates the previously empty `mozkey-ibg-bin` AUR repository.

The workflow pins the AUR Ed25519 host fingerprint published at
<https://aur.archlinux.org/>. A key rotation therefore fails closed until the
new fingerprint has been verified and reviewed here.

For recovery after configuring a missing key, run **Publish AUR package**
manually with the already-published `vX.Y.Z` tag. Draft releases are rejected.

The 0BSD `LICENSE` covers only this AUR packaging metadata. The installed
Mozkey payload retains its upstream and third-party notices under
`/usr/share/licenses/mozkey-ibg/`.
