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
