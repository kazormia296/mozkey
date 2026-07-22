The scripts under this directory are only served as a reference about how the
files is suppose to be installed. The actual path is different on distribution
and distribution packagers is suppose to write their own version of install
script.

There is no guranantee that these scripts work for everyone and everywhere.

Grimodex consumer lifecycle
----------------------------

The Fcitx5 Mozkey addon publishes a private atomic
`consumers/fcitx5-mozkey-ibg.json` heartbeat at startup and every 15 minutes.  The
installed `/usr/lib/mozkey-ibg/unregister-grimodex-consumer` helper removes only
that per-user identity using the same fd-relative native registrar as runtime.
The audited uninstaller removes the Mozkey runtime marker before invoking the
helper, so a still-mapped addon cannot resurrect the identity. Stop or reload
Fcitx before package removal when practical, and use the uninstaller with both
`MOZKEY_REMOVE_GRIMODEX_CONSUMER=1` and an explicit absolute
`GRIMODEX_IME_ROOT`.  Project snapshots, Fcitx profiles, and other IME consumer
files are preserved.

Real Fcitx5 process E2E
-----------------------

`launch_fcitx5_mozkey_e2e` is the launcher consumed by Grimodex's ignored
Linux process test. It accepts only the private `GRIMODEX_IME_ROOT`, `HOME`,
and XDG directories created by that test, installs the repository's Mozkey-only
profile into the private `XDG_CONFIG_HOME`, and starts a headless Fcitx5 under
`dbus-run-session`. The legacy `mozc` and `grimodex` addons are disabled while
`mozkey-ibg` is force-enabled, so the test proves that the installed
`/usr/lib/fcitx5/fcitx5-mozkey-ibg.so` creates the real `fcitx5-mozkey-ibg` heartbeat;
it never substitutes `mozc_server` or a fixture writer for the addon.

Install the Mozkey Fcitx5 product and server first. Then run the Grimodex test
with the launcher specified by absolute path:

```sh
GRIMODEX_LINUX_MOZKEY_LAUNCHER=/absolute/path/to/mozkey-ibg/scripts/launch_fcitx5_mozkey_e2e \
  cargo test --manifest-path src-tauri/Cargo.toml -p grimodex-db \
  --test linux_ime_server_e2e -- --ignored --nocapture
```

The launcher intentionally fails before starting Fcitx5 if the canonical
Mozkey addon, server runtime marker, addon metadata, or input-method metadata
is missing from `/usr`.
