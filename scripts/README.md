The scripts under this directory are only served as a reference about how the
files is suppose to be installed. The actual path is different on distribution
and distribution packagers is suppose to write their own version of install
script.

There is no guranantee that these scripts work for everyone and everywhere.

Grimodex consumer lifecycle
----------------------------

The Fcitx5 Mozkey addon publishes a private atomic
`consumers/fcitx5-mozkey.json` heartbeat at startup and every 15 minutes.  The
installed `/usr/lib/mozkey/unregister-grimodex-consumer` helper removes only
that per-user identity using the same fd-relative native registrar as runtime.
The audited uninstaller removes the Mozkey runtime marker before invoking the
helper, so a still-mapped addon cannot resurrect the identity. Stop or reload
Fcitx before package removal when practical, and use the uninstaller with both
`MOZKEY_REMOVE_GRIMODEX_CONSUMER=1` and an explicit absolute
`GRIMODEX_IME_ROOT`.  Project snapshots, Fcitx profiles, and other IME consumer
files are preserved.
