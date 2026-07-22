// Copyright 2026 The Mozkey Authors

#ifndef MOZC_UNIX_FCITX5_GRIMODEX_CONSUMER_REGISTRAR_H_
#define MOZC_UNIX_FCITX5_GRIMODEX_CONSUMER_REGISTRAR_H_

#include <string>

#include "absl/status/status.h"
#include "absl/strings/string_view.h"

namespace mozc::fcitx5 {

// Publishes the Fcitx Mozkey consumer heartbeat used by Grimodex's `auto`
// integration mode.  Writes are private, atomic, and scoped to this product's
// one consumer file; project snapshots and other consumers are never touched.
class GrimodexConsumerRegistrar final {
 public:
  explicit GrimodexConsumerRegistrar(std::string root);

  GrimodexConsumerRegistrar(const GrimodexConsumerRegistrar &) = delete;
  GrimodexConsumerRegistrar &operator=(const GrimodexConsumerRegistrar &) =
      delete;

  // timestamp must be an RFC3339 UTC timestamp produced by the caller's
  // clock, for example 2026-07-17T01:02:03.456Z.
  absl::Status Register(absl::string_view version,
                        absl::string_view timestamp) const;

  // Registers only while the root-owned product runtime marker is still a
  // regular executable.  If an uninstall removes the marker while Fcitx still
  // has the addon mapped, the next refresh removes (rather than resurrects)
  // the consumer file.  The marker is rechecked after publication to close
  // the package-removal race.
  absl::Status RefreshIfInstalled(absl::string_view version,
                                  absl::string_view timestamp,
                                  absl::string_view runtime_marker) const;

  // Removes only consumers/fcitx5-mozkey-ibg.json.  The runtime intentionally
  // does not call this on ordinary Fcitx shutdown so short restarts do not
  // create an integration gap; the uninstall helper calls the equivalent
  // operation explicitly.
  absl::Status Unregister() const;

  const std::string &root() const { return root_; }

  static constexpr absl::string_view kConsumerId = "fcitx5-mozkey-ibg";

 private:
  absl::Status RegisterWithCapabilities(
      absl::string_view version, absl::string_view timestamp,
      bool zenzai_v3_conditions) const;

  std::string root_;
};

}  // namespace mozc::fcitx5

#endif  // MOZC_UNIX_FCITX5_GRIMODEX_CONSUMER_REGISTRAR_H_
