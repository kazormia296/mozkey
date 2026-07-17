# Linux product isolation

Mozkey's OSS Linux build is intentionally installable alongside a distribution
Mozc package. The two products do not share an Fcitx addon identity, profile,
server endpoint, or installed executable directory.

| Resource | Mozkey path or identity |
| --- | --- |
| Fcitx addon | `mozkey`, `fcitx5-mozkey.so` |
| Fcitx input method | `mozkey` |
| Profile | `$XDG_CONFIG_HOME/mozkey`, otherwise `~/.config/mozkey` |
| Legacy profile | `~/.mozkey` |
| Linux abstract IPC prefix | `/tmp/.mozkey.` (the leading slash becomes NUL) |
| Server and tools | `/usr/lib/mozkey` |
| Zenz socket and lock | `~/.mozkey_zenz_scorer_pipe`, `~/.mozkey_zenz_scorer.lock` |
| Zenz runtime | `/usr/lib/mozkey/llama-server` -> distro `/usr/bin/llama-server` |
| Zenz model | `/usr/lib/mozkey/models/zenz-v3.2-small-Q5_K_M.gguf` |

`/usr/lib/mozkey` is used as the product-private libexec directory because the
server, GUI tool, and Zenz scorer are implementation helpers launched by the
frontend and are not general-purpose commands for `$PATH`. The directory also
matches `SystemUtil::GetServerDirectory()` in the OSS Linux Bazel build.

After building the release targets from `src/`, run:

```sh
../scripts/smoke_test_mozkey_fcitx5_install
```

Linux packaging must depend on a compatible `llama-server` provider.  The
default private link targets `/usr/bin/llama-server`; a distro package with a
different absolute path can set `MOZKEY_ZENZ_LLAMA_SERVER_TARGET` while
staging.  The GGUF and its notices are installed from Mozkey's already tracked
Zenz runtime assets, so release mode never depends on an environment override.

The smoke test installs into a temporary `DESTDIR`, validates the Fcitx and
AppStream metadata, repeats the install as an upgrade, removes only the audited
Mozkey manifest, proves Hazkey/Mozc/user-data sentinels survive, and reinstalls
the product.  The real uninstall entry point is:

```sh
sudo env PREFIX=/usr ./scripts/uninstall_mozkey_fcitx5
```

It deliberately preserves `$XDG_CONFIG_HOME/mozkey`, `~/.config/mozkey`, the
legacy profile, Zenz models, and Grimodex Protocol v1 snapshots. Windows and
macOS product paths are unchanged.

To remove only this product's consumer heartbeat during a root-owned package
uninstall, pass the user's absolute Protocol v1 root explicitly:

```sh
sudo env PREFIX=/usr MOZKEY_REMOVE_GRIMODEX_CONSUMER=1 \
  GRIMODEX_IME_ROOT="$HOME/.local/share/com.miyakey.grimodex/ime" \
  ./scripts/uninstall_mozkey_fcitx5
```

The uninstaller resolves the root owner before removing the runtime marker and
uses `setpriv` to run the native unregister helper as that owner.  The helper
still performs its fd-relative ownership, mode, and symlink checks; it removes
only `consumers/fcitx5-mozkey.json`.

## Real Fcitx5 consumer E2E

Grimodex's ignored Linux process test must use the installed addon rather than
launch `mozc_server` directly. After installing the Mozkey Fcitx5 and server
products, point `GRIMODEX_LINUX_MOZKEY_LAUNCHER` at
`scripts/launch_fcitx5_mozkey_e2e` in this checkout. The launcher preserves the
test-provided `GRIMODEX_IME_ROOT`, `HOME`, and XDG homes, installs a private
Mozkey-only Fcitx profile, and starts foreground Fcitx5 on a new D-Bus session.
Detection of `consumers/fcitx5-mozkey.json` therefore proves the actual addon
was loaded and executed its startup heartbeat.
