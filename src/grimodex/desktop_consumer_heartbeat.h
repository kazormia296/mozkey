// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_DESKTOP_CONSUMER_HEARTBEAT_H_
#define MOZC_GRIMODEX_DESKTOP_CONSUMER_HEARTBEAT_H_

#include <memory>

#include "absl/status/status.h"
#include "absl/strings/string_view.h"

namespace mozc::grimodex {

namespace desktop_consumer_heartbeat_internal {

// Kept as a pure predicate so branding behavior is testable without touching
// a real per-user consumer directory.
constexpr bool ShouldEnable(bool is_mozkey_build,
                            bool is_supported_desktop_platform) {
  return is_mozkey_build && is_supported_desktop_platform;
}

}  // namespace desktop_consumer_heartbeat_internal

// Opaque process-lifetime owner.  Destruction stops and joins its timer thread
// but intentionally leaves the last consumer file in place.
class DesktopConsumerHeartbeat {
 public:
  virtual ~DesktopConsumerHeartbeat() = default;

  DesktopConsumerHeartbeat(const DesktopConsumerHeartbeat &) = delete;
  DesktopConsumerHeartbeat &operator=(const DesktopConsumerHeartbeat &) =
      delete;

 protected:
  DesktopConsumerHeartbeat() = default;
};

// Starts the current desktop platform's one process heartbeat.  Windows uses
// tsf-mozkey-ibg and macOS uses imkit-mozkey-ibg.  Other platforms and
// Google-branded builds return nullptr.
// Resolution and publication failures are logged and never abort the server.
std::unique_ptr<DesktopConsumerHeartbeat> StartDesktopConsumerHeartbeat();

// Explicit uninstall API.  It ignores the runtime test-root override, removes
// only this platform's consumer file from the canonical per-user root, and is
// never called by ordinary server shutdown.  Google-branded desktop builds are
// a successful no-op.
absl::Status UnregisterDesktopConsumer();

// Windows Installer commit actions do not have a trustworthy user environment
// block.  This variant resolves the canonical root from MSI's AppDataFolder
// value instead of resolving FOLDERID_RoamingAppData for the current user.
// Other platforms return an unsupported status; Google-branded builds are a
// successful no-op.
absl::Status UnregisterWindowsDesktopConsumerForAppData(
    absl::string_view app_data_directory);

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_DESKTOP_CONSUMER_HEARTBEAT_H_
