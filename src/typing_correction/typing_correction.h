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

#ifndef MOZC_TYPING_CORRECTION_TYPING_CORRECTION_H_
#define MOZC_TYPING_CORRECTION_TYPING_CORRECTION_H_

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "absl/strings/string_view.h"
#include "absl/types/span.h"
#include "protocol/commands.pb.h"
#include "typing_correction/generated_contract.h"

namespace mozc::typing_correction {

enum class InputMethod {
  kRoman,
  kKana,
};

enum class Operation {
  kLiteralReplacement,
  kAdjacentTranspose,
  kNeighborSubstitution,
  kDuplicateRemoval,
  kMissingKeyInsertion,
  kKanaModifier,
  kInputModeReplay,
};

struct Edit {
  size_t raw_start = 0;
  size_t raw_length = 0;
  std::string replacement;
  Operation operation = Operation::kLiteralReplacement;
  int32_t cost = 0;
  std::string rule_id;
};

// A raw-input hypothesis is deliberately independent of a converter result.
// This keeps the original composition available while later stages decide
// whether the corrected reading is worth showing or applying.
struct Hypothesis {
  std::string original_raw;
  std::string corrected_raw;
  std::string original_reading;
  std::string corrected_reading;
  std::vector<Edit> edits;
  // Non-empty only for key-trace corrections (currently JIS kana).  Roman
  // corrections replay corrected_raw through the original Table instead.
  std::vector<commands::KeyEvent> corrected_key_events;
  int32_t edit_cost = 0;
  bool auto_applicable = false;
};

struct Limits {
  size_t max_edits = kTypingCorrectionMaxEdits;
  size_t max_raw_hypotheses = kTypingCorrectionMaxRawHypotheses;
  size_t max_reading_hypotheses = kTypingCorrectionMaxReadingHypotheses;
  int32_t max_edit_cost = kTypingCorrectionMaxEditCost;
};

// These checks are kept separate from candidate generation so callers can
// make the safety decision before touching a Composer or converter.
struct RomanInputGateContext {
  bool feature_enabled = false;
  bool is_roman_input = true;
  bool cursor_at_end = true;
  bool secure_input = false;
  bool reverse_conversion = false;
  bool ascii_input_mode = false;
  bool mixed_script = false;
  bool url_like = false;
  bool email_like = false;
  bool path_like = false;
  bool default_roman_table = true;
  size_t max_raw_bytes = kTypingCorrectionMaxRawBytes;
};

struct KanaInputGateContext {
  bool feature_enabled = false;
  bool is_jis_kana_input = false;
  bool cursor_at_end = true;
  bool secure_input = false;
  bool reverse_conversion = false;
  bool ascii_input_mode = false;
  bool mixed_script = false;
  bool modifier_insensitive_conversion = false;
  size_t max_key_events = kTypingCorrectionMaxKeyEvents;
};

// Safety gate for the candidate-only replay that interprets a physical kana
// trace through the Roman table.  This path is intentionally separate from
// the normal Roman/Kana correction gates because the source trace may contain
// kana strings even while the configured preedit method is Roman.
struct KanaModeMismatchInputGateContext {
  bool feature_enabled = false;
  bool cursor_at_end = true;
  bool secure_input = false;
  bool reverse_conversion = false;
  bool ascii_input_mode = false;
  size_t max_key_events = kTypingCorrectionMaxKeyEvents;
};

bool IsEligibleForRomanCorrection(const RomanInputGateContext& context,
                                  absl::string_view raw);

bool IsEligibleForKanaCorrection(const KanaInputGateContext& context,
                                 absl::Span<const commands::KeyEvent> events,
                                 absl::string_view raw);

bool IsEligibleForKanaModeMismatch(
    const KanaModeMismatchInputGateContext& context,
    absl::Span<const commands::KeyEvent> events, absl::string_view raw);

const char* OperationName(Operation operation);

}  // namespace mozc::typing_correction

#endif  // MOZC_TYPING_CORRECTION_TYPING_CORRECTION_H_
