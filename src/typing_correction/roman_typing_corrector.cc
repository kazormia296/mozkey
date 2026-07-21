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

#include "typing_correction/roman_typing_corrector.h"

#include <algorithm>
#include <cstddef>
#include <queue>
#include <string>
#include <utility>
#include <vector>

#include "absl/strings/string_view.h"
#include "typing_correction/generated_roman_rules.h"

namespace mozc::typing_correction {
namespace {

bool IsLowerAscii(const char character) {
  return character >= 'a' && character <= 'z';
}

absl::string_view QwertyNeighbors(const char character) {
  switch (character) {
    case 'q':
      return "was";
    case 'w':
      return "qesa";
    case 'e':
      return "wsdr";
    case 'r':
      return "edft";
    case 't':
      return "rfgy";
    case 'y':
      return "tghu";
    case 'u':
      return "yhji";
    case 'i':
      return "ujko";
    case 'o':
      return "iklp";
    case 'p':
      return "ol";
    case 'a':
      return "qwsz";
    case 's':
      return "awedxz";
    case 'd':
      return "serfcx";
    case 'f':
      return "drtgvc";
    case 'g':
      return "ftyhbv";
    case 'h':
      return "gyujnb";
    case 'j':
      return "huikmn";
    case 'k':
      return "jiolm";
    case 'l':
      return "kop";
    case 'z':
      return "asx";
    case 'x':
      return "zsdc";
    case 'c':
      return "xdfv";
    case 'v':
      return "cfgb";
    case 'b':
      return "vghn";
    case 'n':
      return "bhjm";
    case 'm':
      return "njk";
    default:
      return {};
  }
}

Hypothesis MakeHypothesis(absl::string_view raw, size_t raw_start,
                          size_t raw_length, absl::string_view replacement,
                          Operation operation, int cost, bool auto_applicable,
                          absl::string_view rule_id) {
  Hypothesis result;
  result.original_raw = std::string(raw);
  result.corrected_raw = std::string(raw);
  result.corrected_raw.replace(raw_start, raw_length, replacement.data(),
                               replacement.size());
  result.edits.push_back(Edit{raw_start,
                              raw_length,
                              std::string(replacement),
                              operation,
                              cost,
                              std::string(rule_id)});
  result.edit_cost = cost;
  result.auto_applicable = auto_applicable;
  return result;
}

bool IsBetter(const Hypothesis& lhs, const Hypothesis& rhs) {
  if (lhs.auto_applicable != rhs.auto_applicable) {
    return lhs.auto_applicable;
  }
  const bool lhs_is_generic =
      lhs.edits.front().rule_id.starts_with("GEN-");
  const bool rhs_is_generic =
      rhs.edits.front().rule_id.starts_with("GEN-");
  if (lhs_is_generic != rhs_is_generic) {
    return !lhs_is_generic;
  }
  if (lhs.edit_cost != rhs.edit_cost) {
    return lhs.edit_cost < rhs.edit_cost;
  }
  return lhs.corrected_raw < rhs.corrected_raw;
}

void AddCandidate(absl::string_view raw, const Limits& limits,
                  Hypothesis candidate, std::vector<Hypothesis>* candidates) {
  if (candidate.corrected_raw == raw || candidate.edits.empty() ||
      candidate.edits.size() > limits.max_edits ||
      candidate.edit_cost > limits.max_edit_cost) {
    return;
  }

  for (Hypothesis& existing : *candidates) {
    if (existing.corrected_raw == candidate.corrected_raw) {
      if (IsBetter(candidate, existing)) {
        existing = std::move(candidate);
      }
      return;
    }
  }
  candidates->push_back(std::move(candidate));
}

struct WorseHypothesisFirst {
  bool operator()(const Hypothesis& lhs, const Hypothesis& rhs) const {
    return IsBetter(lhs, rhs);
  }
};

}  // namespace

absl::Span<const RomanRule> DefaultRomanRules() {
  return absl::Span<const RomanRule>(kGeneratedRomanRules,
                                     kGeneratedRomanRuleCount);
}

std::vector<Hypothesis> RomanTypingCorrector::Generate(
    const absl::string_view raw) const {
  return Generate(raw, Limits());
}

std::vector<Hypothesis> RomanTypingCorrector::Generate(
    const absl::string_view raw, const Limits& limits) const {
  std::vector<Hypothesis> candidates;
  if (raw.empty() || limits.max_edits == 0 ||
      limits.max_raw_hypotheses == 0 || limits.max_edit_cost < 0) {
    return candidates;
  }

  // The initial production contract permits one edit.  Keeping this guard
  // here makes an accidental increase of max_edits fail closed until the
  // multi-edit search has its own pruning and tests.
  if (limits.max_edits != 1) {
    return candidates;
  }

  for (const RomanRule& rule : rules_) {
    if (rule.wrong.empty() || rule.wrong == rule.corrected) {
      continue;
    }
    if (rule.whole_input_only) {
      if (raw != rule.wrong) {
        continue;
      }
      AddCandidate(raw, limits,
                   MakeHypothesis(raw, 0, raw.size(), rule.corrected,
                                  rule.operation, rule.cost,
                                  rule.auto_applicable, rule.rule_id),
                   &candidates);
      continue;
    }

    size_t position = raw.find(rule.wrong);
    while (position != absl::string_view::npos) {
      AddCandidate(raw, limits,
                   MakeHypothesis(raw, position, rule.wrong.size(),
                                  rule.corrected, rule.operation, rule.cost,
                                  rule.auto_applicable, rule.rule_id),
                   &candidates);
      position = raw.find(rule.wrong, position + 1);
    }
  }

  // Generic operations are candidate-only until a converter-backed ranking
  // stage proves that the correction is safe.  Corpus mappings above may
  // still promote an identical raw result to auto_applicable.
  if (raw.size() >= 2) {
    for (size_t index = 0; index + 1 < raw.size(); ++index) {
      if (!IsLowerAscii(raw[index]) || !IsLowerAscii(raw[index + 1]) ||
          raw[index] == raw[index + 1]) {
        continue;
      }
      std::string replacement(raw.substr(index, 2));
      std::swap(replacement[0], replacement[1]);
      AddCandidate(raw, limits,
                   MakeHypothesis(raw, index, 2, replacement,
                                  Operation::kAdjacentTranspose, 100, false,
                                  "GEN-TRANSPOSE"),
                   &candidates);
    }
  }

  for (size_t index = 0; index < raw.size(); ++index) {
    if (!IsLowerAscii(raw[index])) {
      continue;
    }

    for (const char neighbor : QwertyNeighbors(raw[index])) {
      std::string replacement(1, neighbor);
      AddCandidate(raw, limits,
                   MakeHypothesis(raw, index, 1, replacement,
                                  Operation::kNeighborSubstitution, 120, false,
                                  "GEN-QWERTY"),
                   &candidates);
    }

    if (index > 0 && raw[index - 1] == raw[index]) {
      AddCandidate(raw, limits,
                   MakeHypothesis(raw, index, 1, "",
                                  Operation::kDuplicateRemoval, 110, false,
                                  "GEN-DUPLICATE"),
                   &candidates);
    }
  }

  // Deduplication above is exact, while the final bounded selection uses a
  // priority queue so candidate-window limits do not depend on rule order.
  std::priority_queue<Hypothesis, std::vector<Hypothesis>,
                      WorseHypothesisFirst>
      best_candidates;
  for (Hypothesis& candidate : candidates) {
    best_candidates.push(std::move(candidate));
    if (best_candidates.size() > limits.max_raw_hypotheses) {
      best_candidates.pop();
    }
  }
  candidates.clear();
  while (!best_candidates.empty()) {
    candidates.push_back(best_candidates.top());
    best_candidates.pop();
  }
  std::sort(candidates.begin(), candidates.end(),
            [](const Hypothesis& lhs, const Hypothesis& rhs) {
              return IsBetter(lhs, rhs);
            });
  return candidates;
}

}  // namespace mozc::typing_correction
