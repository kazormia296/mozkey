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

#ifndef MOZC_TYPING_CORRECTION_KANA_TYPING_CORRECTOR_H_
#define MOZC_TYPING_CORRECTION_KANA_TYPING_CORRECTOR_H_

#include <cstdint>
#include <vector>

#include "absl/strings/string_view.h"
#include "absl/types/span.h"
#include "protocol/commands.pb.h"
#include "typing_correction/typing_correction.h"

namespace mozc::typing_correction {

struct KanaRule {
  int32_t wrong_key_code = -1;
  absl::string_view wrong_key_string;
  int32_t corrected_key_code = -1;
  absl::string_view corrected_key_string;
  Operation operation = Operation::kNeighborSubstitution;
  int32_t cost = 0;
  bool auto_applicable = false;
  absl::string_view rule_id;
};

struct KanaGoldCase {
  absl::string_view case_id;
  absl::string_view typed_raw;
  absl::string_view corrected_raw;
  absl::string_view typed_key_string;
  absl::string_view corrected_key_string;
  Operation operation = Operation::kNeighborSubstitution;
  bool auto_applicable = false;
  absl::string_view stratum;
};

struct KanaNegativeCase {
  absl::string_view case_id;
  absl::string_view typed_raw;
  absl::string_view typed_key_string;
  bool raw_forbidden = false;
  bool display_forbidden = false;
  bool auto_forbidden = true;
  absl::string_view stratum;
};

absl::Span<const KanaRule> DefaultKanaRules();
absl::Span<const KanaGoldCase> KanaGoldCases();
absl::Span<const KanaNegativeCase> KanaNegativeCases();

// Generates bounded one-event JIS-kana hypotheses.  The key_string is
// treated as part of the event identity: a missed Shift/dakuten can change
// the resulting reading even when the physical key_code is unchanged.
class KanaTypingCorrector final {
 public:
  KanaTypingCorrector() = default;
  explicit KanaTypingCorrector(absl::Span<const KanaRule> rules)
      : rules_(rules) {}

  std::vector<Hypothesis> Generate(
      absl::Span<const commands::KeyEvent> events) const;
  std::vector<Hypothesis> Generate(
      absl::Span<const commands::KeyEvent> events,
      const Limits& limits) const;

 private:
  absl::Span<const KanaRule> rules_ = DefaultKanaRules();
};

// The unshifted JIS kana map is public for corpus/tests and for diagnostics.
absl::string_view JisKanaKeyString(int32_t key_code);
absl::string_view JisKanaPhysicalNeighbors(int32_t key_code);

}  // namespace mozc::typing_correction

#endif  // MOZC_TYPING_CORRECTION_KANA_TYPING_CORRECTOR_H_
