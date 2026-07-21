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

#include "typing_correction/composer_replayer.h"

#include <cstddef>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace mozc::typing_correction {
namespace {

bool ContainsAsciiLetter(absl::string_view value) {
  for (const char character : value) {
    if ((character >= 'a' && character <= 'z') ||
        (character >= 'A' && character <= 'Z')) {
      return true;
    }
  }
  return false;
}

}  // namespace

std::optional<composer::Composer> BuildCorrectedComposer(
    const composer::Composer& original, const absl::string_view corrected_raw) {
  if (corrected_raw.empty() || corrected_raw == original.GetRawString()) {
    return std::nullopt;
  }

  composer::Composer replay = original;
  replay.EditErase();
  replay.InsertCharacter(std::string(corrected_raw));

  // A replay is valid only if the current Composer/Table accepted the whole
  // raw sequence.  In particular, unresolved ASCII tail characters must not
  // be silently passed to the normal converter.
  if (replay.GetRawString() != corrected_raw) {
    return std::nullopt;
  }
  const std::string corrected_reading = replay.GetQueryForConversion();
  if (corrected_reading.empty() || ContainsAsciiLetter(corrected_reading) ||
      corrected_reading == original.GetQueryForConversion()) {
    return std::nullopt;
  }

  return replay;
}

std::optional<composer::Composer> BuildCorrectedComposer(
    const composer::Composer& original, const Hypothesis& hypothesis) {
  if (!hypothesis.corrected_key_events.empty()) {
    if (hypothesis.corrected_raw.empty()) {
      return std::nullopt;
    }

    composer::Composer replay = original;
    replay.EditErase();
    for (const commands::KeyEvent& event : hypothesis.corrected_key_events) {
      if (!replay.InsertCharacterKeyEvent(event)) {
        return std::nullopt;
      }
    }
    if (replay.GetRawString() != hypothesis.corrected_raw ||
        replay.GetQueryForConversion().empty() ||
        replay.GetQueryForConversion() == original.GetQueryForConversion()) {
      return std::nullopt;
    }
    return replay;
  }
  return BuildCorrectedComposer(original, hypothesis.corrected_raw);
}

std::vector<Hypothesis> GenerateRomanCorrectionHypotheses(
    const composer::Composer& original, const RomanInputGateContext& context,
    const Limits& limits, IncrementalRomanCache* cache) {
  std::vector<Hypothesis> result;
  const std::string original_raw = original.GetRawString();
  if (!IsEligibleForRomanCorrection(context, original_raw) ||
      limits.max_reading_hypotheses == 0) {
    return result;
  }

  const RomanTypingCorrector corrector;
  const std::vector<Hypothesis> raw_hypotheses =
      cache == nullptr ? corrector.Generate(original_raw, limits)
                        : cache->GetOrGenerate(original_raw, limits, corrector);
  const std::string original_reading = original.GetQueryForConversion();
  std::unordered_set<std::string> readings;
  for (Hypothesis hypothesis : raw_hypotheses) {
    if (!context.default_roman_table && !hypothesis.edits.empty() &&
        !hypothesis.edits.front().rule_id.starts_with("GEN-")) {
      continue;
    }
    const std::optional<composer::Composer> replay =
        BuildCorrectedComposer(original, hypothesis);
    if (!replay.has_value()) {
      continue;
    }
    hypothesis.original_reading = original_reading;
    hypothesis.corrected_reading = replay->GetQueryForConversion();
    if (!readings.insert(hypothesis.corrected_reading).second) {
      continue;
    }
    result.push_back(std::move(hypothesis));
    if (result.size() >= limits.max_reading_hypotheses) {
      break;
    }
  }
  return result;
}

std::vector<Hypothesis> GenerateKanaCorrectionHypotheses(
    const composer::Composer& original, const KanaInputGateContext& context,
    const Limits& limits) {
  std::vector<Hypothesis> result;
  const absl::Span<const commands::KeyEvent> trace =
      original.GetKeyEventTrace();
  const std::string original_raw = original.GetRawString();
  if (!IsEligibleForKanaCorrection(context, trace, original_raw) ||
      limits.max_reading_hypotheses == 0) {
    return result;
  }

  const KanaTypingCorrector corrector;
  const std::vector<Hypothesis> raw_hypotheses =
      corrector.Generate(trace, limits);
  const std::string original_reading = original.GetQueryForConversion();
  std::unordered_set<std::string> readings;
  for (Hypothesis hypothesis : raw_hypotheses) {
    const std::optional<composer::Composer> replay =
        BuildCorrectedComposer(original, hypothesis);
    if (!replay.has_value()) {
      continue;
    }
    hypothesis.original_reading = original_reading;
    hypothesis.corrected_reading = replay->GetQueryForConversion();
    if (!readings.insert(hypothesis.corrected_reading).second) {
      continue;
    }
    result.push_back(std::move(hypothesis));
    if (result.size() >= limits.max_reading_hypotheses) {
      break;
    }
  }
  return result;
}

std::optional<composer::Composer> BuildRomanModeReplayForKana(
    const composer::Composer& original) {
  const absl::Span<const commands::KeyEvent> trace =
      original.GetKeyEventTrace();
  if (trace.empty()) {
    return std::nullopt;
  }

  composer::Composer replay = original;
  replay.EditErase();
  for (const commands::KeyEvent& source_event : trace) {
    if (!source_event.has_key_code()) {
      return std::nullopt;
    }
    commands::KeyEvent roman_event = source_event;
    roman_event.clear_key_string();
    if (!replay.InsertCharacterKeyEvent(roman_event)) {
      return std::nullopt;
    }
  }
  if (replay.GetQueryForConversion().empty() ||
      replay.GetQueryForConversion() == original.GetQueryForConversion()) {
    return std::nullopt;
  }
  return replay;
}

std::vector<Hypothesis> GenerateKanaModeMismatchHypotheses(
    const composer::Composer& original,
    const KanaModeMismatchInputGateContext& context, const Limits& limits) {
  std::vector<Hypothesis> result;
  if (limits.max_reading_hypotheses == 0 ||
      !IsEligibleForKanaModeMismatch(context, original.GetKeyEventTrace(),
                                     original.GetRawString())) {
    return result;
  }

  const std::optional<composer::Composer> replay =
      BuildRomanModeReplayForKana(original);
  if (!replay.has_value()) {
    return result;
  }

  Hypothesis hypothesis;
  hypothesis.original_raw = original.GetRawString();
  hypothesis.corrected_raw = replay->GetRawString();
  hypothesis.original_reading = original.GetQueryForConversion();
  hypothesis.corrected_reading = replay->GetQueryForConversion();
  hypothesis.corrected_key_events.clear();
  for (const commands::KeyEvent& source_event : original.GetKeyEventTrace()) {
    commands::KeyEvent roman_event = source_event;
    roman_event.clear_key_string();
    hypothesis.corrected_key_events.push_back(std::move(roman_event));
  }
  hypothesis.edits.push_back(Edit{0,
                                  hypothesis.original_raw.size(),
                                  hypothesis.corrected_raw,
                                  Operation::kInputModeReplay,
                                  220,
                                  "GEN-MODE-REPLAY"});
  hypothesis.edit_cost = 220;
  hypothesis.auto_applicable = false;
  result.push_back(std::move(hypothesis));
  return result;
}

}  // namespace mozc::typing_correction
