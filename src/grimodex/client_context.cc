// Copyright 2026 The Mozkey Authors

#include "grimodex/client_context.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

#include "protocol/commands.pb.h"

namespace mozc::grimodex {
namespace {

std::string BoundedMetadata(std::string_view value, size_t limit) {
  return std::string(value.substr(0, std::min(value.size(), limit)));
}

}  // namespace

uint64_t AdvanceFocusEpoch(uint64_t current) {
  ++current;
  return current == 0 ? 1 : current;
}

int32_t RevisionFromFocusEpoch(uint64_t focus_epoch) {
  const uint32_t revision = static_cast<uint32_t>(focus_epoch & 0x7FFFFFFFU);
  return static_cast<int32_t>(revision == 0 ? 1 : revision);
}

commands::Context BuildClientContext(
    std::string_view program, std::string_view frontend, bool secure_input,
    uint64_t focus_epoch,
    const SurroundingTextProvider& surrounding_text_provider) {
  if (focus_epoch == 0) {
    focus_epoch = 1;
  }

  commands::Context context;
  commands::GrimodexClientContext* grimodex = context.mutable_grimodex();
  grimodex->set_program(BoundedMetadata(program, kMaxClientProgramBytes));
  grimodex->set_frontend(BoundedMetadata(frontend, kMaxClientFrontendBytes));
  grimodex->set_secure_input(secure_input);
  grimodex->set_focus_epoch(focus_epoch);
  context.set_revision(RevisionFromFocusEpoch(focus_epoch));

  if (secure_input) {
    context.set_input_field_type(commands::Context::PASSWORD);
    return context;
  }

  if (surrounding_text_provider) {
    if (const std::optional<SurroundingText> surrounding_text =
            surrounding_text_provider()) {
      context.set_preceding_text(surrounding_text->preceding_text);
      context.set_following_text(surrounding_text->following_text);
    }
  }
  return context;
}

bool IsValidClientContext(const commands::Context& context) {
  if (!context.has_grimodex() || !context.grimodex().has_focus_epoch() ||
      context.grimodex().focus_epoch() == 0 || !context.has_revision() ||
      context.revision() == 0) {
    return false;
  }
  if (context.revision() !=
      RevisionFromFocusEpoch(context.grimodex().focus_epoch())) {
    return false;
  }
  if (context.grimodex().program().size() > kMaxClientProgramBytes ||
      context.grimodex().frontend().size() > kMaxClientFrontendBytes) {
    return false;
  }

  if (context.grimodex().secure_input()) {
    return context.input_field_type() == commands::Context::PASSWORD &&
           !context.has_preceding_text() && !context.has_following_text();
  }
  return context.input_field_type() != commands::Context::PASSWORD;
}

}  // namespace mozc::grimodex
