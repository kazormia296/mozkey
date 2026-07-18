# Third-party notices

This product includes third-party software and model files.

## Zenz v3.2 small GGUF

- Repository: Miwa-Keita/zenz-v3.2-small-gguf
- Source: Hugging Face
- Original model file: ggml-model-Q5_K_M.gguf
- Windows included file: models/zenz-v3.2-small-Q5_K_M.gguf
- Linux included file: /usr/lib/mozkey/models/zenz-v3.2-small-Q5_K_M.gguf
- License: Apache License 2.0
- Source repository URL: https://huggingface.co/Miwa-Keita/zenz-v3.2-small-gguf
- Source file URL: https://huggingface.co/Miwa-Keita/zenz-v3.2-small-gguf/blob/c67e03e07d215c869f591b274c1631170d3e11fe/ggml-model-Q5_K_M.gguf
- Source commit: c67e03e07d215c869f591b274c1631170d3e11fe
- Source SHA-256: 29c223d4c23327b80fd13ebb5ab2555057a46317997d5da391584ffbef0db673
- Windows notes: Distributed unchanged except for placement, naming, and MSI packaging.
- Linux modification notice (2026-07-17): Mozkey deterministically changes
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
- Windows bundled files:
  - llama-server.exe
  - llama.dll
  - ggml.dll
  - ggml-base.dll
  - ggml-cpu.dll
- License: MIT License
- Linux runtime: the product links to a compatible distribution-provided
  `llama-server`; its package must provide the same MIT license notice.
- Notes: Used as local inference runtime components for the bundled Zenz GGUF model.

## Privacy note

Zenz live correction is designed to run locally. The bundled runtime is started as a local process and the HTTP inference endpoint is expected to bind to 127.0.0.1 only. User input is not intentionally sent to external servers by this feature.

Zenz feedback learning can be disabled in the settings UI. Left context is sanitized before being included in prompts; sensitive-looking context such as URLs, email addresses, file paths, tokens, and long numeric identifiers is rejected before prompt construction.
