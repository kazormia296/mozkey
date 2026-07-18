# Grimodex Protocol v1 on Windows and macOS

Mozkey implements the same Grimodex Snapshot Protocol v1 contract in its
Windows TSF and macOS IMKit frontends. Platform code is limited to secure file
access, native input-context collection, candidate-window callbacks, and
installer lifecycle hooks; snapshot parsing, validation, application scope,
immutable dictionary publication, and consumer-handshake semantics are shared.

The generally published desktop artifact remains the Windows MSI. macOS
packages are built and tested by native CI, but this repository does not yet
publish a generally available Developer ID-signed and notarized installer.

## Runtime contract

- The engine reads `state.json`, the selected project snapshot, and
  `state.json` again. It publishes a new immutable generation only when the two
  state reads agree and every Protocol v1 size, schema, identifier, digest, and
  entry limit passes.
- Windows resolves the default root below
  `%APPDATA%\com.miyakey.grimodex\ime` and validates canonical paths,
  current-user ACLs, file identity, reparse-point and hard-link constraints.
  macOS resolves
  `~/Library/Application Support/com.miyakey.grimodex/ime` and requires
  user-owned private directories and regular files without symlink traversal.
- `GRIMODEX_IME_ROOT` is a runtime/test override. Uninstallers deliberately
  ignore it and attempt to remove only the platform's consumer record from the
  canonical per-user root.
- Project data is scoped to exact `grimodex` or
  `com.miyakey.grimodex` application identities by default.
  `MOZKEY_GRIMODEX_SCOPE=all` is the explicit all-application opt-in; `off` and
  unrecognized nonempty values disable project integration.

## Native frontend security

TSF and IMKit attach bounded program/frontend metadata, secure-input state, and
a focus generation to the typed Mozc context. Secure input is represented as a
password context and never reads or sends surrounding text. Focus changes,
secure-state changes, and document/view changes invalidate prior generations.

Asynchronous edits and renderer callbacks are accepted only for the focused,
non-secure generation that produced them. Candidate selection and highlight
callbacks also carry a renderer token, so a click from a replaced candidate
view cannot be rebound to the current composition.

## Consumer and package lifecycle

Mozkey-branded desktop server processes publish a private, atomic consumer
heartbeat every 15 minutes:

- Windows: `consumers/tsf-mozkey.json`
- macOS: `consumers/imkit-mozkey.json`

The Windows MSI uses a checked commit action for final removal. It stops only
the exact installed `mozc_server.exe`, protects against PID reuse, and requires
two consecutive process snapshots proving that executable absent before the
secure registrar may remove `tsf-mozkey`. If quiescence or registrar validation
fails, uninstall reports failure and preserves the heartbeat instead of
silently completing with an unsafe or immediately republished record.

The macOS uninstaller first boots the per-user Converter LaunchAgent out of
the GUI domain and verifies that `MozcConverter` has exited. It removes the
detached Zenz scorer process group (including its `llama-server` child) and
verifies that the product-owned executables have exited before deleting the
bundle. Only after those gates and successful bundle deletion does it make a
best-effort request through the secure registrar to remove the `imkit-mozkey`
heartbeat. If registrar ownership, mode, or symlink validation rejects the
record, uninstall can finish while leaving that record for manual inspection;
there is no unsafe recursive-delete fallback.

Ordinary process shutdown leaves the final record in place to avoid a short
capability gap during restart. After a successful explicit uninstall, Windows
uses the checked cleanup above and macOS uses its conditional best-effort
cleanup; both target only their platform record. Project snapshots and other
IME records are preserved. Google-branded builds do not publish Mozkey consumer
records.

Zenz capability publication is fail closed. It is enabled only when the
packaged scorer, model, and local llama.cpp runtime are all present. Windows
packages use the bundled named-pipe scorer path. The macOS arm64 application
package stages an arm64-only scorer and CPU/Accelerate `llama-server`, normalized
model, and licenses inside `MozcConverter.app/Contents/Resources`. CI proves
usability by checking package layout, code signatures, the exact arm64
architecture and deployment target, and real inference in the shipped package.

## Release gates

The platform workflows run the shared Protocol v1 fixtures in addition to
native reader and adapter tests. The focused contract commands are:

```powershell
# From src on Windows.
bazelisk test //grimodex:portable_protocol_v1_tests `
  //grimodex:protocol_v1_windows_secure_reader_test `
  //grimodex:consumer_file_registrar_windows_test `
  //protocol:renderer_callback_provenance_test `
  //win32/tip:tip_edit_session_test `
  //win32/tip:tip_grimodex_client_context_test `
  //win32/tip:tip_grimodex_context_util_test `
  //win32/tip:tip_input_mode_manager_test -c dbg --test_output=errors
```

```bash
# From src on macOS.
bazelisk test //grimodex:portable_protocol_v1_tests \
  //grimodex:protocol_v1_secure_reader_test \
  //grimodex:consumer_file_registrar_posix_test \
  //mac:mozc_imk_input_controller_test \
  //mac:renderer_receiver_test \
  //renderer:candidate_view_callback_test -c dbg --test_output=errors
```

Windows x64, Universal, and ARM64 jobs verify that `Mozc64.msi` is a nontrivial
OLE Compound File before upload, and missing artifacts fail the workflow.
The macOS arm64 job probes the packaged Zenz layout and exercises real inference
before publishing its CI artifact.
