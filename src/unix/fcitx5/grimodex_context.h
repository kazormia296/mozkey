// Copyright 2026 The Mozkey Authors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef MOZC_UNIX_FCITX5_GRIMODEX_CONTEXT_H_
#define MOZC_UNIX_FCITX5_GRIMODEX_CONTEXT_H_

#include <cstddef>
#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <string_view>

#include "protocol/commands.pb.h"
#include "unix/fcitx5/mozc_client_interface.h"

namespace fcitx {

// Fcitx metadata is untrusted input from a frontend.  Keep the command wire
// bounded even when a broken or hostile frontend reports a huge identifier.
inline constexpr size_t kMaxGrimodexProgramBytes = 256;
inline constexpr size_t kMaxGrimodexFrontendBytes = 128;

struct GrimodexSurroundingText {
  std::string preceding_text;
  std::string following_text;
};

using GrimodexSurroundingTextProvider =
    std::function<std::optional<GrimodexSurroundingText>()>;

// Returns a nonzero epoch.  The counter is per InputContext and is advanced on
// every focus-in and input-domain transition.
uint64_t AdvanceGrimodexFocusEpoch(uint64_t current);

// Mozc's revision field is signed, so use the lower 31 bits and reserve zero.
int32_t GrimodexRevisionFromFocusEpoch(uint64_t focus_epoch);

// This is the only builder for Context in the Fcitx adapter.  In secure input
// it deliberately does not invoke |surrounding_text_provider|, which also
// prevents lazy loading or calling the clipboard addon.
mozc::commands::Context BuildGrimodexContext(
    std::string_view program, std::string_view frontend, bool secure_input,
    uint64_t focus_epoch,
    const GrimodexSurroundingTextProvider& surrounding_text_provider = {});

// All key and session-command dispatch goes through these functions.  They
// reject a missing or inconsistent typed context instead of falling back to a
// contextless Mozc call.
bool SendKeyWithGrimodexContext(MozcClientInterface* client,
                                const mozc::commands::KeyEvent& key,
                                const mozc::commands::Context& context,
                                mozc::commands::Output* output);
bool SendCommandWithGrimodexContext(
    MozcClientInterface* client,
    const mozc::commands::SessionCommand& command,
    const mozc::commands::Context& context, mozc::commands::Output* output);

}  // namespace fcitx

#endif  // MOZC_UNIX_FCITX5_GRIMODEX_CONTEXT_H_
