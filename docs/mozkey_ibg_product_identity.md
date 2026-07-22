# Mozkey IbG product identity

Mozkey IbG is installed and registered as a product distinct from the upstream
Mozkey fork. The public slug is `mozkey-ibg`, the logical prefix is
`MozkeyIbG`, and the user-facing short name is **Mozkey IbG**.

This identity split intentionally does not migrate profiles, registrations, or
installation state from earlier development builds. Mozkey IbG has not had a
formal release, so installers only own the new identifiers listed here.

## Cross-platform contract

| Surface | Linux | Windows | macOS |
| --- | --- | --- | --- |
| Release asset | `mozkey-ibg-vX.Y.Z-*`, `mozkey-ibg_*`, `mozkey-ibg-*` | `MozkeyIbG_vX.Y.Z_*.msi` | `MozkeyIbG_vX.Y.Z_macos_arm64.pkg` |
| Install root | `/usr/lib/mozkey-ibg` | `Program Files (x86)/MozkeyIbG` | `/Library/Input Methods/MozkeyIbG.app` |
| User profile | `$XDG_CONFIG_HOME/mozkey-ibg` or `~/.mozkey-ibg` | `%LOCALAPPDATA%/Mozkey IbG` and `AppData/LocalLow/MozkeyIbG` feedback | `Application Support/MozkeyIbG` |
| Grimodex consumer | `fcitx5-mozkey-ibg` | `tsf-mozkey-ibg` | `imkit-mozkey-ibg` |
| Runtime namespace | `mozkey-ibg.event.*`, `/tmp/.mozkey-ibg.*`, `.mozkey-ibg_zenz_*` | `Local\\MozkeyIbG.*`, `\\\\.\\pipe\\mozkey-ibg.*`, `\\\\.\\pipe\\mozkey-ibg_zenz_scorer` | `MozkeyIbG.event.*`, `io.github.kazormia296.mozkey-ibg.*`, `.mozkey-ibg_zenz_*` |

## Linux

- Package names: `mozkey-ibg`, `mozkey-ibg-bin`.
- Fcitx addon/input-method ID: `mozkey-ibg`.
- Fcitx shared library: `fcitx5-mozkey-ibg.so`.
- Fcitx metadata: `mozkey-ibg.conf`, `mozkey-ibg-addon.conf`.
- AppStream ID: `io.github.kazormia296.MozkeyIbG`.
- Product data, licenses, icons, and bookkeeping use `mozkey-ibg` paths.

The installed executable basenames remain `mozc_server`, `mozc_tool`, and
related upstream-compatible names. They live under `/usr/lib/mozkey-ibg`, and
their profile, event, socket, and IPC namespaces are distinct, so the basenames
do not prevent concurrent operation.

## Windows

- MSI UpgradeCode: `{422E6070-C76C-4F9B-96BE-FD9569E4B762}`.
- TSF text service: `{2D046FEA-2B23-4E77-946B-FC2AF48219DC}`.
- TSF language profile: `{A5F4AF8E-7338-4A5C-9186-FF5B05B28393}`.
- Service name: `MozkeyIbGCacheService`.
- Registry root: `Software\\Grimodex\\MozkeyIbG`.
- Startup entry: `Mozkey IbG Prelauncher`.
- Firewall rules: `Mozkey IbG Offline - ...`.

All TSF auxiliary GUIDs are also fork-specific. Windows executable basenames
remain upstream-compatible, but they are installed below `Program Files (x86)/MozkeyIbG`
and every registration, service, singleton, IPC endpoint, window class, and
uninstall action resolves the Mozkey IbG product path. The MSI does not own the
shared Terminal Server `SysProcs` values keyed only by an executable basename.

## macOS

- Main bundle: `/Library/Input Methods/MozkeyIbG.app`.
- Bundle namespace: `io.github.kazormia296.mozkey-ibg.inputmethod.Japanese`.
- Package receipt: `io.github.kazormia296.mozkey-ibg.pkg.JapaneseInput`.
- Helper apps: `MozkeyIbGConverter`, `MozkeyIbGRenderer`, `MozkeyIbGTool`, and
  `MozkeyIbGPrelauncher`.
- LaunchAgent labels and filenames use
  `io.github.kazormia296.mozkey-ibg.inputmethod.Japanese.*`.

The uninstaller removes only these Mozkey IbG paths and labels. It does not
remove upstream Mozkey bundles, launch agents, profiles, or Grimodex consumers.

## Co-installation rule

Installing or uninstalling Mozkey IbG must not create, replace, unregister, or
delete an upstream Mozkey product identifier. A contract test checks the
platform identifiers and rejects the known upstream registration values in the
live Mozkey IbG packaging/runtime surfaces.
