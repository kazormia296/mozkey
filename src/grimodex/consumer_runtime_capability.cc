// Copyright 2026 The Mozkey Authors

#include "grimodex/consumer_runtime_capability.h"

#include <array>
#include <functional>

#include "absl/strings/string_view.h"

namespace mozc::grimodex {

bool HasCompleteWindowsZenzRuntime(
    const std::function<bool(absl::string_view)> &file_probe) {
  constexpr std::array<absl::string_view, 7> kRequiredFiles = {
      "mozc_zenz_scorer.exe",
      "llama-server.exe",
      "ggml.dll",
      "ggml-base.dll",
      "ggml-cpu.dll",
      "llama.dll",
      "models/zenz-v3.2-small-Q5_K_M.gguf",
  };
  for (const absl::string_view path : kRequiredFiles) {
    if (!file_probe(path)) {
      return false;
    }
  }
  return true;
}

}  // namespace mozc::grimodex
