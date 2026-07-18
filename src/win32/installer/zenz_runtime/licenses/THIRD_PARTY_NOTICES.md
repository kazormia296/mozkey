# Third-party notices

This product includes third-party software and model files.

## Zenz v3.2 small GGUF

- Repository: Miwa-Keita/zenz-v3.2-small-gguf
- Source: Hugging Face
- Original model file: ggml-model-Q5_K_M.gguf
- Windows included file: models/zenz-v3.2-small-Q5_K_M.gguf
- Linux included file: /usr/lib/mozkey/models/zenz-v3.2-small-Q5_K_M.gguf
- macOS included file: MozcConverter.app/Contents/Resources/models/zenz-v3.2-small-Q5_K_M.gguf
- License: Apache License 2.0
- Source repository URL: https://huggingface.co/Miwa-Keita/zenz-v3.2-small-gguf
- Source file URL: https://huggingface.co/Miwa-Keita/zenz-v3.2-small-gguf/blob/c67e03e07d215c869f591b274c1631170d3e11fe/ggml-model-Q5_K_M.gguf
- Source commit: c67e03e07d215c869f591b274c1631170d3e11fe
- Source SHA-256: 29c223d4c23327b80fd13ebb5ab2555057a46317997d5da391584ffbef0db673
- Windows, Linux, and macOS modification notice (2026-07-17): Mozkey deterministically changes
  only `tokenizer.ggml.pre`, `tokenizer.ggml.bos_token_id`,
  `tokenizer.ggml.eos_token_id`, and `tokenizer.ggml.unknown_token_id` so the
  fixed model loads in unmodified upstream llama.cpp. Tensor descriptors and
  tensor data are byte-for-byte unchanged. The derived GGUF SHA-256 is
  `601572033a0c231857864ab0a2ccf40fbd1abe6ee4ccecd5399bf82e3e559772`; the
  tensor-data SHA-256 is
  `e943d954852ad629c01a278ee61ec4b80b26401d18695e01ef5da5610429b67e`.
  `tools/release/zenz_gguf_normalization.lock.json` pins the transformation.

## llama.cpp / ggml runtime

- Project: ggml-org/llama.cpp
- Source repository URL: https://github.com/ggml-org/llama.cpp
- Windows runtime: `llama-server.exe` is built as a static CPU runtime from the
  official llama.cpp `b9637` source archive (commit
  `aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3`), pinned by SHA-256
  `762283319feb3de30886dc850d42f0e426b06600e7f9639d34e06506597309ca`.
  Separate x64 and ARM64 release inputs are generated and their PE machine,
  CLI contract, file digest, CMake options, and toolchain are recorded in the
  packaged `runtime-manifest.json`. The CPU-only build disables CURL, RPC,
  dynamic backends, CUDA, HIP, Vulkan, SYCL, OpenCL, OpenVINO, WebGPU, tests,
  examples, the unified app, multimodal video, and the Web UI; only the
  `llama-server` target is built and staged.
  The universal MSI intentionally uses the x64 runtime alongside its x64 core
  processes and adds ARM64/ARM64EC TIP bridges; the native ARM64 MSI uses the
  separately built ARM64 runtime.
- License: MIT License
- Linux runtime: the product links to a compatible distribution-provided
  `llama-server`; its package must provide the same MIT license notice.
- macOS runtime: `llama-server` is built from the official llama.cpp `b9637`
  source archive, pinned by SHA-256
  `762283319feb3de30886dc850d42f0e426b06600e7f9639d34e06506597309ca`.
  The package contains an arm64-only static CPU/Accelerate build with deployment
  target 12.0. Metal, CURL, RPC, dynamic backends, multimodal video, tests,
  examples, the unified app, and the Web UI are disabled. The tools subtree is
  configured because b9637 defines `llama-server` there, but only the
  `llama-server` target is built and staged; no other llama.cpp tool binaries
  are packaged.
- Notes: Used as local inference runtime components for the bundled Zenz GGUF model.

## cpp-httplib

- Project: yhirose/cpp-httplib
- Version vendored by llama.cpp `b9637`: 0.47.0
- Source repository URL: https://github.com/yhirose/cpp-httplib
- License: MIT License
- Packaged license file: `cpp-httplib-MIT.txt`
- License file SHA-256:
  `f8c53951438545b8ed61176d9071bd1039e81502f9ec9590b85ccd5c71a08473`
- Notes: Used by `llama-server` for its local HTTP endpoint.

## JSON for Modern C++ (nlohmann/json)

- Project: nlohmann/json
- Version vendored by llama.cpp `b9637`: 3.12.0
- Source repository URL: https://github.com/nlohmann/json
- License: MIT License
- Packaged license file: `nlohmann-json-MIT.txt`
- License file SHA-256:
  `c0d068392ea65358b798b8c165103560f06e9e3b38c4ab4e2d8810a7b931af86`
- Notes: Used by `llama-server` for JSON request and response handling.

## Privacy note

Zenz live correction is designed to run locally. The bundled runtime is started as a local process and the HTTP inference endpoint is expected to bind to 127.0.0.1 only. User input is not intentionally sent to external servers by this feature.

Zenz feedback learning can be disabled in the settings UI. Left context is sanitized before being included in prompts; sensitive-looking context such as URLs, email addresses, file paths, tokens, and long numeric identifiers is rejected before prompt construction.
