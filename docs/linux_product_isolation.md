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

`/usr/lib/mozkey` is used as the product-private libexec directory because the
server, GUI tool, and Zenz scorer are implementation helpers launched by the
frontend and are not general-purpose commands for `$PATH`. The directory also
matches `SystemUtil::GetServerDirectory()` in the OSS Linux Bazel build.

After building the release targets from `src/`, run:

```sh
../scripts/smoke_test_mozkey_fcitx5_install
```

The smoke test installs into a temporary `DESTDIR`, validates the Fcitx and
AppStream metadata, and fails if any system Mozc-owned path is created. Windows
and macOS product paths are unchanged.
