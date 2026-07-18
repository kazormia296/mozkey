// Copyright 2026 The Mozkey Authors

#ifndef MOZC_WIN32_TIP_TIP_GRIMODEX_CONTEXT_UTIL_H_
#define MOZC_WIN32_TIP_TIP_GRIMODEX_CONTEXT_UTIL_H_

#include <cstdint>
#include <functional>
#include <string>
#include <string_view>

#include "grimodex/client_context.h"
#include "protocol/commands.pb.h"

struct ITfContext;

namespace mozc::client {
class ClientInterface;
}  // namespace mozc::client

namespace mozc::win32::tsf {

class TipTextService;
class TipThreadContext;

// Identifies the TSF input domain in which a value was produced.  Both fields
// are needed: the Grimodex epoch covers application/secure-domain changes,
// while the TSF revision also covers a focus move before the new application
// identity has been resolved.
struct TsfFocusSnapshot final {
  uint64_t focus_epoch = 0;
  int32_t focus_revision = 0;
};

TsfFocusSnapshot CaptureTsfFocusSnapshot(
    const TipThreadContext* thread_context);
bool IsTsfFocusSnapshotCurrent(const TipThreadContext* thread_context,
                               TsfFocusSnapshot snapshot);
bool IsNonSecureTsfFocusSnapshotCurrent(
    const TipThreadContext* thread_context, TsfFocusSnapshot snapshot);
bool IsTsfContextFocusedForSnapshot(TipTextService* text_service,
                                    ITfContext* expected_context,
                                    TsfFocusSnapshot snapshot,
                                    bool require_nonsecure);

// Returns the closest at most 20 UTF-16 code units around the cursor.  A
// valid surrogate pair is never split at the truncation boundary.
std::wstring LimitTsfPrecedingText(std::wstring_view text);
std::wstring LimitTsfFollowingText(std::wstring_view text);

// Returns the normalized executable basename attached to a TSF context, or an
// empty string when Windows cannot authoritatively resolve that process.
std::string GetAttachedProgram(ITfContext* context);

// Builds the current typed TSF domain context.  Password scope is determined
// before any surrounding-text API can be called.
commands::Context BuildTsfMozcContext(TipTextService* text_service,
                                      ITfContext* context,
                                      bool include_surrounding_text);

// Provider-based implementation shared with deterministic reentrancy tests.
// A provider is an external/reentrant boundary.  If either provider changes
// the focus token or secure-input state, its result is discarded and a secure
// context without application text is returned.
using TsfAttachedProgramProvider = std::function<std::string()>;
commands::Context BuildTsfMozcContextFromProviders(
    TipThreadContext* thread_context, bool include_surrounding_text,
    const TsfAttachedProgramProvider& attached_program_provider,
    const grimodex::SurroundingTextProvider& surrounding_text_provider);

// Sends a typed RESET_CONTEXT without reading application text.  This makes a
// focus or secure-input transition visible to the server even when the user
// does not press another key.
bool SendTsfResetContext(TipTextService* text_service, ITfContext* context,
                         client::ClientInterface* client,
                         bool force_secure_input = false,
                         TsfFocusSnapshot* installed_snapshot = nullptr);

// Sends RESET_CONTEXT for an already resolved program without performing any
// further TSF/Win32 queries.  Async callers can resolve the program, validate
// their scheduled focus token, and then use this overload without a reentrant
// query between validation and the command.
bool SendTsfResetContextForProgram(TipTextService* text_service,
                                   std::string_view program,
                                   client::ClientInterface* client,
                                   bool secure_input,
                                   TsfFocusSnapshot* installed_snapshot =
                                       nullptr);

// Revokes a non-focused context's private client without changing the shared
// active TSF domain tracker. The client session is independent, while the
// thread-wide epoch belongs exclusively to the focused document.
bool SendTsfResetContextForInactiveClient(TipTextService* text_service,
                                          client::ClientInterface* client);

// Sends an active-domain command with the current typed TSF context and no
// surrounding text.  Callers that can outlive their scheduling domain should
// capture a Context and call SendCommandWithContext directly instead.
bool SendTsfCommand(TipTextService* text_service, ITfContext* context,
                    client::ClientInterface* client,
                    const commands::SessionCommand& command,
                    commands::Output* output, TsfFocusSnapshot snapshot,
                    bool require_nonsecure,
                    uint64_t* output_application_generation = nullptr);

}  // namespace mozc::win32::tsf

#endif  // MOZC_WIN32_TIP_TIP_GRIMODEX_CONTEXT_UTIL_H_
