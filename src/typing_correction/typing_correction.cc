// Copyright 2026 Grimodex contributors.
//
// Redistribution and use in source and binary forms, with or without
// modification, are permitted provided that the following conditions are met:
//
//     * Redistributions of source code must retain the above copyright
// notice, this list of conditions and the following disclaimer.
//     * Redistributions in binary form must reproduce the above copyright
// notice, this list of conditions and the following disclaimer in the
// documentation and/or other materials provided with the distribution.
//     * Neither the name of the copyright holder nor the names of its
// contributors may be used to endorse or promote products derived from
// this software without specific prior written permission.
//
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
// AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
// IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
// ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
// LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
// CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
// SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
// INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
// CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
// ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
// POSSIBILITY OF SUCH DAMAGE.

#include "typing_correction/typing_correction.h"

namespace mozc::typing_correction {
namespace {

bool IsLowerAscii(absl::string_view value) {
  if (value.empty()) {
    return false;
  }
  for (const char character : value) {
    if (character < 'a' || character > 'z') {
      return false;
    }
  }
  return true;
}

}  // namespace

bool IsEligibleForRomanCorrection(const RomanInputGateContext& context,
                                  const absl::string_view raw) {
  if (!context.feature_enabled || !context.is_roman_input ||
      !context.cursor_at_end || context.secure_input ||
      context.reverse_conversion || context.ascii_input_mode ||
      context.mixed_script || context.url_like || context.email_like ||
      context.path_like) {
    return false;
  }
  if (raw.size() < kTypingCorrectionMinRawBytes ||
      raw.size() > context.max_raw_bytes) {
    return false;
  }
  return IsLowerAscii(raw);
}

bool IsEligibleForKanaCorrection(
    const KanaInputGateContext& context,
    const absl::Span<const commands::KeyEvent> events,
    const absl::string_view raw) {
  if (!context.feature_enabled || !context.is_jis_kana_input ||
      !context.cursor_at_end || context.secure_input ||
      context.reverse_conversion || context.ascii_input_mode ||
      context.mixed_script || context.modifier_insensitive_conversion) {
    return false;
  }
  if (events.empty() || events.size() > context.max_key_events || raw.empty()) {
    return false;
  }
  for (const commands::KeyEvent& event : events) {
    if (!event.has_key_code() || !event.has_key_string() ||
        event.key_string().empty() || event.modifier_keys_size() != 0) {
      return false;
    }
  }
  return true;
}

bool IsEligibleForKanaModeMismatch(
    const KanaModeMismatchInputGateContext& context,
    const absl::Span<const commands::KeyEvent> events,
    const absl::string_view raw) {
  if (!context.feature_enabled || !context.cursor_at_end ||
      context.secure_input || context.reverse_conversion ||
      context.ascii_input_mode || events.empty() || raw.empty() ||
      events.size() > context.max_key_events) {
    return false;
  }
  for (const commands::KeyEvent& event : events) {
    if (!event.has_key_code() || !event.has_key_string() ||
        event.key_string().empty() || event.modifier_keys_size() != 0) {
      return false;
    }
  }
  return true;
}

const char* OperationName(const Operation operation) {
  switch (operation) {
    case Operation::kLiteralReplacement:
      return "literal_replacement";
    case Operation::kAdjacentTranspose:
      return "transpose";
    case Operation::kNeighborSubstitution:
      return "neighbor_substitution";
    case Operation::kDuplicateRemoval:
      return "duplicate_removal";
    case Operation::kMissingKeyInsertion:
      return "missing_key_insertion";
    case Operation::kKanaModifier:
      return "kana_modifier";
    case Operation::kInputModeReplay:
      return "input_mode_replay";
  }
  return "unknown";
}

}  // namespace mozc::typing_correction
