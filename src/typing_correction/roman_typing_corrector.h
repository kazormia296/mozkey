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

#ifndef MOZC_TYPING_CORRECTION_ROMAN_TYPING_CORRECTOR_H_
#define MOZC_TYPING_CORRECTION_ROMAN_TYPING_CORRECTOR_H_

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "absl/strings/string_view.h"
#include "absl/types/span.h"
#include "typing_correction/typing_correction.h"

namespace mozc::typing_correction {

struct RomanRule {
  absl::string_view wrong;
  absl::string_view corrected;
  Operation operation = Operation::kLiteralReplacement;
  int32_t cost = 0;
  bool auto_applicable = false;
  bool whole_input_only = false;
  absl::string_view rule_id;
};

struct RomanGoldCase {
  absl::string_view case_id;
  absl::string_view typed_raw;
  absl::string_view corrected_raw;
  absl::string_view expected_reading;
  Operation operation = Operation::kLiteralReplacement;
  bool auto_applicable = false;
};

struct RomanNegativeCase {
  absl::string_view case_id;
  absl::string_view typed_raw;
};

absl::Span<const RomanRule> DefaultRomanRules();

class RomanTypingCorrector final {
 public:
  RomanTypingCorrector() = default;
  explicit RomanTypingCorrector(absl::Span<const RomanRule> rules)
      : rules_(rules) {}

  std::vector<Hypothesis> Generate(absl::string_view raw) const;
  std::vector<Hypothesis> Generate(absl::string_view raw,
                                   const Limits& limits) const;

 private:
  absl::Span<const RomanRule> rules_ = DefaultRomanRules();
};

}  // namespace mozc::typing_correction

#endif  // MOZC_TYPING_CORRECTION_ROMAN_TYPING_CORRECTOR_H_
