# Linux installed-product dogfood

These probes exercise the installed `/usr/lib/mozkey/mozc_server` and Fcitx
addon on the real desktop session. They use the tracked, non-secret
`release_fixture.json`; callers cannot replace the expected reading, ordinary
Mozc result, or project-dictionary result after observing a run.

Run them from the exact clean checkout used by
`scripts/build_mozkey_linux_bazel archlinux-x86_64`, and install the generated
package payload rather than using a direct source install. The package embeds
`/usr/share/doc/mozkey/linux-build-attestation.json`. Every GUI, restart, and
Electron result binds that document byte-for-byte to the candidate
attestation, the installed addon/server digests, the addon inode actually
mapped executable by Fcitx, and the direct live server child with the requested
scope and Protocol environment. A stale direct install therefore fails even
if its conversion output happens to match.

Each installed-candidate check also revalidates the fresh-profile marker
against the current boot, checkout HEAD/runner blob, profile directory inode,
and exact Fcitx start time. Its marker/root digests are part of every GUI,
restart, and Electron evidence record.
GUI and Electron runners execute the candidate verifier's server-free
`--profile-only` mode before launching the input probe, then compare that
evidence with the live-server result after input. A nonfresh or replaced
profile therefore fails before it can receive a test commit.

Before installation and again before dogfood, verify the candidate itself:

```sh
scripts/verify_mozkey_linux_build_attestation archlinux-x86_64
(cd dist && \
  sha256sum -c mozkey-v0.8.0-archlinux-x86_64.tar.xz.sha256)
cmp dist/linux/archlinux-x86_64/build-attestation.json \
  dist/mozkey-v0.8.0-archlinux-x86_64.build-attestation.json
```

## Isolated Protocol fixture and fresh Mozkey profile

Create new private Protocol and profile roots for each Fcitx scope row, before
starting Fcitx. The tracked runner must mark the profile while it is still
empty; the gate later binds that marker's boot clock, checkout HEAD/blob, and
directory inode to the exact Fcitx start time. Reusing an old private directory
is intentionally rejected even when its permissions look correct.

```sh
mozkey_head=$(git rev-parse HEAD)
protocol_root=$(mktemp -d "${XDG_RUNTIME_DIR}/mozkey-protocol.XXXXXX")
chmod 0700 "${protocol_root}"
tools/release/linux_dogfood/prepare_protocol_fixture.py \
  --root "${protocol_root}"

profile_root=$(mktemp -d "${XDG_RUNTIME_DIR}/mozkey-profile.XXXXXX")
chmod 0700 "${profile_root}"
tools/release/linux_dogfood/run_server_restart_gate.py \
  --prepare-fresh-profile \
  --profile-root "${profile_root}" \
  --expected-head "${mozkey_head}"
export MOZKEY_DOGFOOD_PROFILE_ROOT="${profile_root}"
```

Start the one installed `/usr/bin/fcitx5` with
`GRIMODEX_IME_ROOT=${protocol_root}` and
both `HOME=${profile_root}` and `XDG_CONFIG_HOME=${profile_root}`. Mozkey
intentionally honors an existing `$HOME/.mozkey` for backward compatibility;
the private HOME prevents that legacy profile from bypassing the release
fixture. The marker and every candidate check require `.mozkey` to remain
absent. Omit `MOZKEY_GRIMODEX_SCOPE` for the default
gate; restart Fcitx with the exact values `all`, `off`, and a fixed unsupported
value for the other scope gates. The runners verify the live Fcitx PID, start
time, executable, exact scope environment, Protocol root, and fresh profile
root before and after input. They also bind the `org.freedesktop.IBus`
well-known name to that exact Fcitx lifetime through the D-Bus daemon. Restore
the user's normal Fcitx environment after the matrix.

Do not carry `mozc_server` across scope rows: stop Fcitx, run the exact pidfd
runtime stop gate while the installed paths still exist, then start the next
Fcitx lifetime with its new environment. Run the normal GTK/Qt/Electron case
before its secure-field companion so the row has an attested live server.

```sh
fcitx5-remote -e
scripts/stop_mozkey_linux_runtime
env HOME="${profile_root}" XDG_CONFIG_HOME="${profile_root}" \
  GRIMODEX_IME_ROOT="${protocol_root}" MOZKEY_GRIMODEX_SCOPE=all \
  /usr/bin/fcitx5 -d
```

The headless IBus restart gate requires exactly one `CommitText`, every key
consumed, no forwarded key, exact custom output, a unique killed installed
server, and a different replacement server lifetime. Before any custom commit,
it proves the ordinary/default result with no pre-existing server and no
`$XDG_CONFIG_HOME/mozkey` state. This ordering prevents the custom canary from
teaching Mozc the answer that is later used as fallback evidence. The gate also
restores and verifies the prior focused Fcitx method/state.

```sh
mozkey_head=$(git rev-parse HEAD)

tools/release/linux_dogfood/run_server_restart_gate.py \
  --iterations 3 --pause-after 3 \
  --protocol-root "${protocol_root}" \
  --profile-root "${profile_root}" \
  --expected-head "${mozkey_head}"
```

## Private input injector

Use a dedicated ydotoold socket in a private runtime directory. The daemon
must run as the desktop user with exact executable `/usr/bin/ydotoold`; the
socket must be owned by that user with mode 0600.

```sh
ydotool_root=$(mktemp -d "${XDG_RUNTIME_DIR}/mozkey-ydotool.XXXXXX")
chmod 0700 "${ydotool_root}"
ydotool_socket=${ydotool_root}/socket
/usr/bin/ydotoold --socket-path="${ydotool_socket}" --socket-perm=0600 \
  >"${ydotool_root}/daemon.log" 2>&1 &
ydotool_pid=$!
export YDOTOOL_SOCKET=${ydotool_socket}
export MOZKEY_DOGFOOD_YDOTOOLD_PID=${ydotool_pid}
```

Every input operation holds a pidfd across the exact `/usr/bin/ydotool`
command and compares the full daemon/socket identity before and after. All
commands and state restoration are bounded. The reading and converted text are
never printed by the input helper.

## GTK and Qt

`run_gui_scope_gate.py` builds the committed adjacent source in a fresh
private directory. It pins system compilers/pkg-config, a real Wayland
session, the Fcitx input modules, probe process identity, checkout HEAD/blobs,
the fixed fixture, and the input helper transcript. It covers exact committed
text, default/all/off/unknown scope behavior, focus during the sequence, and a
secure password-field variant.

```sh
tools/release/linux_dogfood/run_gui_scope_gate.py \
  --toolkit gtk --scope-mode default \
  --protocol-root "${protocol_root}" \
  --profile-root "${profile_root}"

tools/release/linux_dogfood/run_gui_scope_gate.py \
  --toolkit qt --scope-mode default --secure \
  --protocol-root "${protocol_root}" \
  --profile-root "${profile_root}"
```

Run both toolkits for each matching live Fcitx scope. For `unknown`, also pass
`--unknown-scope-value` with the exact unsupported value used to start Fcitx.
The runner deliberately labels its toolkit program identity as requested, not
observed: backend-specific Fcitx program/frontend binding, preedit rendering,
candidate click, and cross-window focus switching remain separate interactive
Gate 9 evidence.

For a compile-only check, use disposable output names rather than fixed `/tmp`
paths:

```sh
dogfood_build_dir=$(mktemp -d /tmp/mozkey-dogfood-build.XXXXXX)
cc tools/release/linux_dogfood/gtk_probe.c \
  $(pkg-config --cflags --libs gtk4) \
  -o "${dogfood_build_dir}/gtk-probe"
c++ -fPIC tools/release/linux_dogfood/qt_probe.cc \
  $(pkg-config --cflags --libs Qt6Widgets) \
  -o "${dogfood_build_dir}/qt-probe"
```

## Packaged and development Electron

`electron_scope_probe.mjs` verifies the Mozkey harness HEAD/blobs, Grimodex
HEAD/clean state, exact Electron binary and app artifact hashes, the live
Electron `/proc` executable, fixed Protocol fixture, dedicated generated
user-data directory, one focused BrowserWindow and active input, and stable
Fcitx identity. It verifies that `app.getPath("userData")` is the exact private
gate directory and binds the `org.freedesktop.IBus` owner PID/start/executable
to that Fcitx lifetime before and after input and cleanup. The packaged gate
manifests the complete bundle root, including
`app.asar.unpacked` and `extraResources`. The development gate manifests
`dist-electron`, `dist`, the native `.node` addon, the release `grimodex-mcp`
sidecar, and semantic resources as one runtime. Electron and the input helper
receive separate allowlisted environments; neither receives the expected
custom/default values.

Required environment variables are:

- `MOZKEY_DOGFOOD_ELECTRON_KIND`: `packaged` or `development`;
- `MOZKEY_DOGFOOD_ELECTRON_BINARY` and its
  `MOZKEY_DOGFOOD_ELECTRON_SHA256`;
- `MOZKEY_DOGFOOD_ELECTRON_ARTIFACT` and its
  `MOZKEY_DOGFOOD_ELECTRON_ARTIFACT_SHA256`;
- `MOZKEY_DOGFOOD_ELECTRON_RUNTIME_SHA256`, generated by the matching runtime
  manifest command below;
- `MOZKEY_DOGFOOD_GRIMODEX_REPOSITORY` and full
  `MOZKEY_DOGFOOD_GRIMODEX_HEAD`;
- `MOZKEY_DOGFOOD_MOZKEY_REPOSITORY` and full
  `MOZKEY_DOGFOOD_MOZKEY_HEAD`;
- `MOZKEY_DOGFOOD_PLAYWRIGHT_SHA256`, generated by `--manifest-tree` for the
  resolved pinned Playwright 1.60.0 package tree;
- `MOZKEY_DOGFOOD_PROTOCOL_ROOT`, `MOZKEY_DOGFOOD_PROFILE_ROOT`,
  `MOZKEY_DOGFOOD_SCOPE_MODE`,
  `YDOTOOL_SOCKET`, and `MOZKEY_DOGFOOD_YDOTOOLD_PID`.

`MOZKEY_DOGFOOD_PROFILE_ROOT` is the exact fresh `XDG_CONFIG_HOME` used to
launch the current Fcitx lifetime.

Development runs additionally set the canonical main file, its containing
`dist-electron` tree and the renderer tree plus both tree digests:

```sh
tools/release/linux_dogfood/electron_scope_probe.mjs \
  --manifest-tree /absolute/grimodex/dist-electron
tools/release/linux_dogfood/electron_scope_probe.mjs \
  --manifest-tree /absolute/grimodex/dist

tools/release/linux_dogfood/electron_scope_probe.mjs \
  --manifest-development-runtime /absolute/grimodex
```

Use the emitted digests as
`MOZKEY_DOGFOOD_ELECTRON_MAIN_ROOT_SHA256` and
`MOZKEY_DOGFOOD_ELECTRON_RENDERER_SHA256`, with the corresponding canonical
root paths. Use the composite `runtime_sha256` result as
`MOZKEY_DOGFOOD_ELECTRON_RUNTIME_SHA256`. Development runs force the exact
release `src-tauri/target/release/grimodex-mcp` through `GRIMODEX_MCP_PATH`, so
build that sidecar and the native addon before generating the manifest.

For a packaged Linux bundle, manifest the exact directory containing the
selected Electron executable:

```sh
tools/release/linux_dogfood/electron_scope_probe.mjs \
  --manifest-packaged-runtime /absolute/Grimodex-linux-unpacked
```

Use that `runtime_sha256` result for the packaged
`MOZKEY_DOGFOOD_ELECTRON_RUNTIME_SHA256`. Set `MOZKEY_DOGFOOD_SECURE=1` for the
password-field run. For `unknown`, set `MOZKEY_DOGFOOD_UNKNOWN_SCOPE_VALUE`
exactly. Run packaged and development variants for every scope mode; only the
normal, non-secure run is expected to apply the custom fixture under an allowed
scope.

The harness snapshots live in their own private runtime directory, are made
read-only before either helper starts, and are identity/blob checked around
each subprocess. A passing result is emitted only after Electron/user-data
cleanup and a second exact installed-candidate/Fcitx/server verification.
Result records contain only identities, hashes, character counts, and booleans.
After the matrix, terminate the exact private ydotoold PID, remove only the
fresh Protocol/profile/ydotool runtime directories, and restart the user's
normal Fcitx process.
