// Copyright 2026 The Mozkey Authors
// Licensed under the Apache License, Version 2.0.

#include "unix/fcitx5/grimodex_context.h"

#include <cstdint>
#include <string_view>

#include "grimodex/client_context.h"
#include "protocol/commands.pb.h"
#include "unix/fcitx5/mozc_client_interface.h"

namespace fcitx {
uint64_t AdvanceGrimodexFocusEpoch(uint64_t current) {
  return mozc::grimodex::AdvanceFocusEpoch(current);
}

int32_t GrimodexRevisionFromFocusEpoch(uint64_t focus_epoch) {
  return mozc::grimodex::RevisionFromFocusEpoch(focus_epoch);
}

mozc::commands::Context BuildGrimodexContext(
    std::string_view program, std::string_view frontend, bool secure_input,
    uint64_t focus_epoch,
    const GrimodexSurroundingTextProvider& surrounding_text_provider) {
  return mozc::grimodex::BuildClientContext(
      program, frontend, secure_input, focus_epoch, surrounding_text_provider);
}

bool SendKeyWithGrimodexContext(MozcClientInterface* client,
                                const mozc::commands::KeyEvent& key,
                                const mozc::commands::Context& context,
                                mozc::commands::Output* output) {
  return client != nullptr && output != nullptr &&
         mozc::grimodex::IsValidClientContext(context) &&
         client->SendKeyWithContext(key, context, output);
}

bool SendCommandWithGrimodexContext(
    MozcClientInterface* client,
    const mozc::commands::SessionCommand& command,
    const mozc::commands::Context& context, mozc::commands::Output* output) {
  return client != nullptr && output != nullptr &&
         mozc::grimodex::IsValidClientContext(context) &&
         client->SendCommandWithContext(command, context, output);
}

}  // namespace fcitx
