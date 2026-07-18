// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_CONSUMER_FILE_REGISTRAR_POSIX_H_
#define MOZC_GRIMODEX_CONSUMER_FILE_REGISTRAR_POSIX_H_

#if !defined(_WIN32)

#include <string>

#include "absl/status/status.h"
#include "absl/strings/string_view.h"
#include "grimodex/consumer_file_registrar.h"
#include "grimodex/consumer_handshake.h"

namespace mozc::grimodex {

// POSIX production writer used by macOS (and available to other Unix
// frontends).  `root` must be a normalized absolute UTF-8 filesystem path.
class PosixConsumerFileRegistrar final : public ConsumerFileRegistrar {
 public:
  explicit PosixConsumerFileRegistrar(std::string root);

  PosixConsumerFileRegistrar(const PosixConsumerFileRegistrar &) = delete;
  PosixConsumerFileRegistrar &operator=(
      const PosixConsumerFileRegistrar &) = delete;

  absl::Status Refresh(const ConsumerHandshake &handshake) const override;
  absl::Status Unregister(
      absl::string_view consumer_id) const override;

  const std::string &root() const { return root_; }

 private:
  std::string root_;
};

}  // namespace mozc::grimodex

#endif  // !defined(_WIN32)

#endif  // MOZC_GRIMODEX_CONSUMER_FILE_REGISTRAR_POSIX_H_
