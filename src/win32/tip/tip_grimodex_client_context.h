// Copyright 2026 The Mozkey Authors

#ifndef MOZC_WIN32_TIP_TIP_GRIMODEX_CLIENT_CONTEXT_H_
#define MOZC_WIN32_TIP_TIP_GRIMODEX_CLIENT_CONTEXT_H_

#include <cstdint>
#include <string>
#include <string_view>

#include "grimodex/client_context.h"
#include "protocol/commands.pb.h"

namespace mozc::win32::tsf {

// Returns the final component of a Windows executable path, lowercases ASCII
// characters, and removes only a final ".exe" suffix.  An unavailable path
// remains an empty/unknown program identifier.
std::string NormalizeExecutableBasename(std::string_view executable_path);

// Tracks the identity of the TSF input domain.  Zero is never emitted, and the
// epoch advances when focus, application identity, or secure-input state
// changes.
class TipGrimodexDomainTracker final {
 public:
  explicit TipGrimodexDomainTracker(uint64_t initial_focus_epoch = 1)
      : focus_epoch_(initial_focus_epoch == 0 ? 1 : initial_focus_epoch) {}

  void OnFocusChanged();
  uint64_t Observe(std::string_view program, bool secure_input);

  uint64_t focus_epoch() const { return focus_epoch_; }

 private:
  uint64_t focus_epoch_;
  bool observed_ = false;
  std::string program_;
  bool secure_input_ = false;
};

// Builds the typed context used by the Windows TSF frontend.
commands::Context BuildTsfClientContext(
    std::string_view program, bool secure_input, uint64_t focus_epoch,
    const grimodex::SurroundingTextProvider& surrounding_text_provider = {});

}  // namespace mozc::win32::tsf

#endif  // MOZC_WIN32_TIP_TIP_GRIMODEX_CLIENT_CONTEXT_H_
