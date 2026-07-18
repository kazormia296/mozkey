// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_CONSUMER_FILE_REGISTRAR_H_
#define MOZC_GRIMODEX_CONSUMER_FILE_REGISTRAR_H_

#include "absl/status/status.h"
#include "absl/strings/string_view.h"
#include "grimodex/consumer_handshake.h"

namespace mozc::grimodex {

// Publishes Protocol v1 consumer heartbeats without modifying state.json,
// projects/, or another consumer's file.  Implementations serialize and
// validate every handshake before touching the filesystem.
class ConsumerFileRegistrar {
 public:
  virtual ~ConsumerFileRegistrar() = default;

  virtual absl::Status Refresh(const ConsumerHandshake &handshake) const = 0;
  virtual absl::Status Unregister(
      absl::string_view consumer_id) const = 0;
};

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_CONSUMER_FILE_REGISTRAR_H_
