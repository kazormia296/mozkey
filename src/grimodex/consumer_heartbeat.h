// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_CONSUMER_HEARTBEAT_H_
#define MOZC_GRIMODEX_CONSUMER_HEARTBEAT_H_

#include <functional>
#include <optional>
#include <string>

#include "absl/status/status.h"
#include "absl/time/time.h"
#include "grimodex/consumer_file_registrar.h"
#include "grimodex/consumer_handshake.h"

namespace mozc::grimodex {

// Formats a Protocol v1 timestamp in UTC with exactly millisecond precision.
std::string FormatConsumerHeartbeatTimestamp(absl::Time now);

// Clock-driven, filesystem-agnostic heartbeat state machine.  The registrar
// is deliberately injected so tests and platform owners can prove that a
// heartbeat only ever writes or removes its own consumer file.
class ConsumerHeartbeat final {
 public:
  using Clock = std::function<absl::Time()>;

  ConsumerHeartbeat(ConsumerFileRegistrar &registrar,
                    ConsumerHandshake metadata, Clock clock);

  ConsumerHeartbeat(const ConsumerHeartbeat &) = delete;
  ConsumerHeartbeat &operator=(const ConsumerHeartbeat &) = delete;

  // Publishes immediately even if the previous write was recent.
  absl::Status RefreshNow();

  // Publishes on first use, at the 900-second boundary, or after clock
  // rollback.  Failed writes do not advance last_success().
  absl::Status RefreshIfDue();

  // Updates the only runtime-dependent capability before the next publish.
  // Desktop owners re-probe each interval so removal of a packaged runtime is
  // reflected instead of advertising a stale capability.
  void SetZenzaiV3ConditionsAvailable(bool available) {
    metadata_.capabilities.zenzai_v3_conditions = available;
  }

  // Explicit uninstall operation.  Ordinary object/process shutdown must not
  // call this so the last heartbeat remains usable for the 2700-second
  // freshness window.
  absl::Status Unregister();

  const std::optional<absl::Time> &last_success() const {
    return last_success_;
  }
  const ConsumerHandshake &metadata() const { return metadata_; }

 private:
  absl::Status RefreshAt(absl::Time now);

  ConsumerFileRegistrar &registrar_;
  ConsumerHandshake metadata_;
  Clock clock_;
  std::optional<absl::Time> last_success_;
};

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_CONSUMER_HEARTBEAT_H_
