// Copyright 2026 The Mozkey Authors

#include "grimodex/consumer_heartbeat.h"

#include <functional>
#include <optional>
#include <string>
#include <utility>

#include "absl/status/status.h"
#include "absl/time/time.h"
#include "grimodex/consumer_file_registrar.h"
#include "grimodex/consumer_handshake.h"

namespace mozc::grimodex {

std::string FormatConsumerHeartbeatTimestamp(absl::Time now) {
  return absl::FormatTime("%Y-%m-%dT%H:%M:%E3SZ", now,
                          absl::UTCTimeZone());
}

ConsumerHeartbeat::ConsumerHeartbeat(ConsumerFileRegistrar &registrar,
                                     ConsumerHandshake metadata, Clock clock)
    : registrar_(registrar),
      metadata_(std::move(metadata)),
      clock_(std::move(clock)) {
  // last_seen is owned exclusively by the injected clock.
  metadata_.last_seen.clear();
}

absl::Status ConsumerHeartbeat::RefreshAt(absl::Time now) {
  ConsumerHandshake handshake = metadata_;
  handshake.last_seen = FormatConsumerHeartbeatTimestamp(now);
  const absl::Status status = registrar_.Refresh(handshake);
  if (status.ok()) {
    last_success_ = now;
  }
  return status;
}

absl::Status ConsumerHeartbeat::RefreshNow() { return RefreshAt(clock_()); }

absl::Status ConsumerHeartbeat::RefreshIfDue() {
  const absl::Time now = clock_();
  if (last_success_.has_value() && !ShouldRefresh(*last_success_, now)) {
    return absl::OkStatus();
  }
  return RefreshAt(now);
}

absl::Status ConsumerHeartbeat::Unregister() {
  return registrar_.Unregister(metadata_.consumer_id);
}

}  // namespace mozc::grimodex
