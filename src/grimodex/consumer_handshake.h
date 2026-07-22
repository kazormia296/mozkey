// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_CONSUMER_HANDSHAKE_H_
#define MOZC_GRIMODEX_CONSUMER_HANDSHAKE_H_

#include <cstddef>
#include <string>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/string_view.h"
#include "absl/time/time.h"

namespace mozc::grimodex {

inline constexpr absl::string_view kFcitx5ConsumerId = "fcitx5-mozkey-ibg";
inline constexpr absl::string_view kTsfConsumerId = "tsf-mozkey-ibg";
inline constexpr absl::string_view kImkitConsumerId = "imkit-mozkey-ibg";

inline constexpr absl::Duration kConsumerRefreshInterval =
    absl::Seconds(900);
inline constexpr absl::Duration kConsumerExpiry = absl::Seconds(2700);
inline constexpr size_t kMaxConsumerHandshakeBytes = 8 * 1024;

struct ConsumerCapabilities {
  bool profile = false;
  bool dynamic_dictionary = false;
  bool zenzai_v3_conditions = false;
  bool application_scoping = false;
};

// Portable representation of a Grimodex Protocol v1 consumer heartbeat.
// `last_seen` is an RFC3339 timestamp in UTC (with a trailing `Z`).
struct ConsumerHandshake {
  std::string consumer_id;
  std::string name;
  std::string version;
  std::string platform;
  std::string last_seen;
  ConsumerCapabilities capabilities;
};

// Validates all metadata before it is used as JSON or as a consumer filename.
absl::Status ValidateConsumerHandshake(const ConsumerHandshake &handshake);

// Returns canonical, newline-terminated Protocol v1 JSON.  Field order and
// escaping are deterministic across platforms.
absl::StatusOr<std::string> SerializeConsumerHandshake(
    const ConsumerHandshake &handshake);

// A clock rollback requests an immediate refresh so that a future-valued
// last-success time cannot suppress heartbeats indefinitely.
bool ShouldRefresh(absl::Time last_success, absl::Time now);

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_CONSUMER_HANDSHAKE_H_
