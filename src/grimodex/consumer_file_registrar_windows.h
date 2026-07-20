// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_CONSUMER_FILE_REGISTRAR_WINDOWS_H_
#define MOZC_GRIMODEX_CONSUMER_FILE_REGISTRAR_WINDOWS_H_

#if defined(_WIN32)

#include <string>

#include "absl/status/status.h"
#include "absl/strings/string_view.h"
#include "grimodex/consumer_file_registrar.h"
#include "grimodex/consumer_handshake.h"

namespace mozc::grimodex {

// Windows production writer for Protocol v1 consumer heartbeats.  Paths are
// UTF-8 at the API boundary and must name an absolute local drive path; UNC
// roots are rejected to preserve the local-only boundary.
class WindowsConsumerFileRegistrar final : public ConsumerFileRegistrar {
 public:
  explicit WindowsConsumerFileRegistrar(std::string root);

  WindowsConsumerFileRegistrar(const WindowsConsumerFileRegistrar &) = delete;
  WindowsConsumerFileRegistrar &operator=(
      const WindowsConsumerFileRegistrar &) = delete;

  absl::Status Refresh(const ConsumerHandshake &handshake) const override;
  absl::Status Unregister(
      absl::string_view consumer_id) const override;

  const std::string &root() const { return root_; }

 private:
  std::string root_;
};

}  // namespace mozc::grimodex

#endif  // defined(_WIN32)

#endif  // MOZC_GRIMODEX_CONSUMER_FILE_REGISTRAR_WINDOWS_H_
