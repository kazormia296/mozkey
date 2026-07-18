// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_CLIENT_CONTEXT_H_
#define MOZC_GRIMODEX_CLIENT_CONTEXT_H_

#include <cstddef>
#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <string_view>

#include "protocol/commands.pb.h"

namespace mozc::grimodex {

// Client metadata comes from a frontend process and must remain bounded on the
// command wire even when that process reports a hostile identifier.
inline constexpr size_t kMaxClientProgramBytes = 256;
inline constexpr size_t kMaxClientFrontendBytes = 128;

struct SurroundingText final {
  std::string preceding_text;
  std::string following_text;
};

using SurroundingTextProvider =
    std::function<std::optional<SurroundingText>()>;

// Advances a per-input-domain counter and reserves zero as an invalid epoch.
uint64_t AdvanceFocusEpoch(uint64_t current);

// Context::revision is signed.  Map the epoch into its positive 31-bit range
// and reserve zero so the server can distinguish an absent revision.
int32_t RevisionFromFocusEpoch(uint64_t focus_epoch);

// Builds the typed Context shared by all desktop frontends.  Surrounding text
// is loaded lazily and the provider is never invoked in secure input.
commands::Context BuildClientContext(
    std::string_view program, std::string_view frontend, bool secure_input,
    uint64_t focus_epoch,
    const SurroundingTextProvider& surrounding_text_provider = {});

// Returns true only for a bounded, internally consistent typed context.  In
// particular, a secure context must be PASSWORD and cannot carry surrounding
// text, while a non-secure context cannot claim PASSWORD input.
bool IsValidClientContext(const commands::Context& context);

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_CLIENT_CONTEXT_H_
