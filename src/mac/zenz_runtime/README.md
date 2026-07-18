# macOS Zenz runtime

`tools/release/prepare_macos_zenz_runtime.py` creates the ignored `generated/`
tree consumed by the macOS package target. The preparation gate:

- verifies the official llama.cpp `b9637` source archive SHA-256;
- builds static CPU/Accelerate-only `arm64` (macOS 11.0) and `x86_64`
  (macOS 10.15) `llama-server` slices, then combines them with `lipo`;
- compiles matching `mozc_zenz_scorer` slices with the same deployment targets
  and stages their explicit `lipo` universal binary;
- disables Metal, CURL, RPC, dynamic backends, multimodal video, tests,
  examples, the unified app, and the server Web UI; the tools subtree is
  configured because b9637 defines `llama-server` there, but the preparation
  tool builds and stages only the explicit `llama-server` target;
- creates and verifies the repository-pinned normalized Zenz GGUF; and
- stages a machine-readable runtime manifest.

The generated tree is deliberately absent from source control. Consequently,
`//mac:package` cannot silently produce a Zenz-enabled package without the
verified runtime inputs.
