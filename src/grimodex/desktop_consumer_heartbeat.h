// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_DESKTOP_CONSUMER_HEARTBEAT_H_
#define MOZC_GRIMODEX_DESKTOP_CONSUMER_HEARTBEAT_H_

#include <memory>

#include "absl/status/status.h"

namespace mozc::grimodex {

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
// tsf-mozkey and macOS uses imkit-mozkey.  Other platforms return nullptr.
// Resolution and publication failures are logged and never abort the server.
std::unique_ptr<DesktopConsumerHeartbeat> StartDesktopConsumerHeartbeat();

// Explicit uninstall API.  It removes only this platform's consumer file and
// is never called by ordinary server shutdown.
absl::Status UnregisterDesktopConsumer();

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_DESKTOP_CONSUMER_HEARTBEAT_H_
