// Copyright 2026 The Mozkey Authors
// Licensed under the Apache License, Version 2.0.

#include "unix/fcitx5/grimodex_context.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

#include "protocol/commands.pb.h"
#include "unix/fcitx5/mozc_client_interface.h"

namespace fcitx {
namespace {

std::string BoundedMetadata(std::string_view value, size_t limit) {
  return std::string(value.substr(0, std::min(value.size(), limit)));
}

bool IsValidGrimodexContext(const mozc::commands::Context& context) {
  if (!context.has_grimodex() || !context.grimodex().has_focus_epoch() ||
      context.grimodex().focus_epoch() == 0 || !context.has_revision() ||
      context.revision() == 0) {
    return false;
  }
  if (context.revision() !=
      GrimodexRevisionFromFocusEpoch(context.grimodex().focus_epoch())) {
    return false;
  }
  if (context.grimodex().program().size() > kMaxGrimodexProgramBytes ||
      context.grimodex().frontend().size() > kMaxGrimodexFrontendBytes) {
    return false;
  }
  if (!context.grimodex().secure_input()) {
    return true;
  }
  return context.input_field_type() == mozc::commands::Context::PASSWORD &&
         !context.has_preceding_text() && !context.has_following_text();
}

}  // namespace

uint64_t AdvanceGrimodexFocusEpoch(uint64_t current) {
  ++current;
  return current == 0 ? 1 : current;
}

int32_t GrimodexRevisionFromFocusEpoch(uint64_t focus_epoch) {
  const uint32_t revision = static_cast<uint32_t>(focus_epoch & 0x7FFFFFFFU);
  return static_cast<int32_t>(revision == 0 ? 1 : revision);
}

mozc::commands::Context BuildGrimodexContext(
    std::string_view program, std::string_view frontend, bool secure_input,
    uint64_t focus_epoch,
    const GrimodexSurroundingTextProvider& surrounding_text_provider) {
  if (focus_epoch == 0) {
    focus_epoch = 1;
  }

  mozc::commands::Context context;
  auto* grimodex = context.mutable_grimodex();
  grimodex->set_program(BoundedMetadata(program, kMaxGrimodexProgramBytes));
  grimodex->set_frontend(BoundedMetadata(frontend, kMaxGrimodexFrontendBytes));
  grimodex->set_secure_input(secure_input);
  grimodex->set_focus_epoch(focus_epoch);
  context.set_revision(GrimodexRevisionFromFocusEpoch(focus_epoch));

  if (secure_input) {
    context.set_input_field_type(mozc::commands::Context::PASSWORD);
    return context;
  }

  if (surrounding_text_provider) {
    if (const auto surrounding_text = surrounding_text_provider()) {
      context.set_preceding_text(surrounding_text->preceding_text);
      context.set_following_text(surrounding_text->following_text);
    }
  }
  return context;
}

bool SendKeyWithGrimodexContext(MozcClientInterface* client,
                                const mozc::commands::KeyEvent& key,
                                const mozc::commands::Context& context,
                                mozc::commands::Output* output) {
  return client != nullptr && output != nullptr &&
         IsValidGrimodexContext(context) &&
         client->SendKeyWithContext(key, context, output);
}

bool SendCommandWithGrimodexContext(
    MozcClientInterface* client,
    const mozc::commands::SessionCommand& command,
    const mozc::commands::Context& context, mozc::commands::Output* output) {
  return client != nullptr && output != nullptr &&
         IsValidGrimodexContext(context) &&
         client->SendCommandWithContext(command, context, output);
}

}  // namespace fcitx
