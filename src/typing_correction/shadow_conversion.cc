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

#include "typing_correction/shadow_conversion.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <utility>
#include <vector>

#include "converter/attribute.h"
#include "converter/inner_segment.h"
#include "typing_correction/composer_replayer.h"

namespace mozc::typing_correction {
namespace {

int32_t SaturatingAdd(const int32_t lhs, const int32_t rhs) {
  if (rhs > 0 && lhs > std::numeric_limits<int32_t>::max() - rhs) {
    return std::numeric_limits<int32_t>::max();
  }
  if (rhs < 0 && lhs < std::numeric_limits<int32_t>::min() - rhs) {
    return std::numeric_limits<int32_t>::min();
  }
  return lhs + rhs;
}

void AppendCandidateStrings(const converter::Candidate& source,
                            std::string* key, std::string* value,
                            std::string* content_key,
                            std::string* content_value,
                            converter::InnerSegmentBoundary* boundaries) {
  const absl::string_view source_key = source.key;
  const absl::string_view source_value = source.value;
  const absl::string_view source_content_key =
      source.content_key.empty() ? source_key : source.content_key;
  const absl::string_view source_content_value =
      source.content_value.empty() ? source_value : source.content_value;

  key->append(source_key);
  value->append(source_value);
  content_key->append(source_content_key);
  content_value->append(source_content_value);

  if (!source.inner_segment_boundary.empty()) {
    boundaries->insert(boundaries->end(), source.inner_segment_boundary.begin(),
                       source.inner_segment_boundary.end());
    return;
  }

  const std::optional<uint32_t> encoded = converter::EncodeLengths(
      static_cast<uint32_t>(source_key.size()),
      static_cast<uint32_t>(source_value.size()),
      static_cast<uint32_t>(source_content_key.size()),
      static_cast<uint32_t>(source_content_value.size()));
  if (encoded.has_value()) {
    boundaries->push_back(*encoded);
  }
}

using StartFunction = bool (ConverterInterface::*)(
    const ConversionRequest&, Segments*) const;

std::optional<WholeSequenceConversion> BuildWholeSequenceWithStart(
    const composer::Composer& original, const Hypothesis& hypothesis,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const StartFunction start_function) {
  if (hypothesis.corrected_raw.empty() || hypothesis.edits.empty() ||
      (hypothesis.corrected_key_events.empty() &&
       hypothesis.corrected_raw == hypothesis.original_raw)) {
    return std::nullopt;
  }

  const std::optional<composer::Composer> corrected_composer =
      BuildCorrectedComposer(original, hypothesis);
  if (!corrected_composer.has_value()) {
    return std::nullopt;
  }

  // SetKey is required because SetConversionRequestView preserves the source
  // request's already-materialized key.  The Composer is nevertheless copied
  // as well, so converter components that inspect ComposerData see the
  // corrected input mode/table state.
  const ConversionRequest corrected_request =
      ConversionRequestBuilder()
          .SetConversionRequestView(source_request)
          .SetComposer(*corrected_composer)
          .SetKey(hypothesis.corrected_reading)
          .Build();

  Segments corrected_segments;
  if (!(converter.*start_function)(corrected_request, &corrected_segments) ||
      corrected_segments.conversion_segments_size() == 0) {
    return std::nullopt;
  }

  WholeSequenceConversion result;
  result.hypothesis = hypothesis;
  result.hypothesis.corrected_reading =
      std::string(corrected_composer->GetQueryForConversion());
  result.candidate.attributes = converter::Attribute::TYPING_CORRECTION;
  result.candidate.description =
      "入力訂正: " + original.GetQueryForConversion() + " → " +
      result.hypothesis.corrected_reading;

  std::string key;
  std::string value;
  std::string content_key;
  std::string content_value;
  for (const converter::Segment& segment :
       corrected_segments.conversion_segments()) {
    if (segment.candidates_size() == 0) {
      return std::nullopt;
    }
    const converter::Candidate& selected = segment.candidate(0);
    if (selected.key.empty() || selected.value.empty()) {
      return std::nullopt;
    }
    const bool first_segment = result.learning_segments.empty();
    AppendCandidateStrings(selected, &key, &value, &content_key,
                           &content_value,
                           &result.candidate.inner_segment_boundary);
    if (first_segment) {
      result.candidate.lid = selected.lid;
    }
    result.candidate.rid = selected.rid;
    result.converter_cost =
        SaturatingAdd(result.converter_cost, selected.cost);
    result.candidate.wcost =
        SaturatingAdd(result.candidate.wcost, selected.wcost);
    result.candidate.structure_cost =
        SaturatingAdd(result.candidate.structure_cost, selected.structure_cost);

    ExternalConversionSegment learning_segment;
    learning_segment.key = std::string(segment.key());
    learning_segment.value = selected.value;
    learning_segment.is_reranked =
        (selected.attributes & converter::Attribute::RERANKED) != 0;
    result.learning_segments.push_back(std::move(learning_segment));
  }

  if (key.empty() || value.empty() || content_key.empty() ||
      content_value.empty()) {
    return std::nullopt;
  }

  result.candidate.key = std::move(key);
  result.candidate.value = std::move(value);
  result.candidate.content_key = std::move(content_key);
  result.candidate.content_value = std::move(content_value);
  result.candidate.cost = result.converter_cost;
  result.candidate.cost_before_rescoring = result.converter_cost;
  result.total_cost = SaturatingAdd(
      result.converter_cost,
      SaturatingAdd(result.hypothesis.edit_cost * kCorrectionCostScale, 0));
  return result;
}

std::vector<WholeSequenceConversion> GenerateShadowForHypotheses(
    const composer::Composer& original, const std::vector<Hypothesis>& hypotheses,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits, const StartFunction start_function) {
  std::vector<WholeSequenceConversion> result;
  result.reserve(hypotheses.size());
  for (const Hypothesis& hypothesis : hypotheses) {
    std::optional<WholeSequenceConversion> conversion =
        BuildWholeSequenceWithStart(original, hypothesis, converter,
                                    source_request, start_function);
    if (!conversion.has_value()) {
      continue;
    }
    result.push_back(std::move(*conversion));
  }

  std::sort(result.begin(), result.end(),
            [](const WholeSequenceConversion& lhs,
               const WholeSequenceConversion& rhs) {
              if (lhs.total_cost != rhs.total_cost) {
                return lhs.total_cost < rhs.total_cost;
              }
              return lhs.candidate.value < rhs.candidate.value;
            });
  if (result.size() > limits.max_reading_hypotheses) {
    result.resize(limits.max_reading_hypotheses);
  }
  return result;
}

std::vector<WholeSequenceConversion> GenerateShadowWithStart(
    const composer::Composer& original, const RomanInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits, const StartFunction start_function,
    IncrementalRomanCache* cache) {
  return GenerateShadowForHypotheses(
      original,
      GenerateRomanCorrectionHypotheses(original, context, limits, cache),
      converter, source_request, limits, start_function);
}

std::vector<WholeSequenceConversion> GenerateKanaShadowWithStart(
    const composer::Composer& original, const KanaInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits, const StartFunction start_function) {
  return GenerateShadowForHypotheses(
      original, GenerateKanaCorrectionHypotheses(original, context, limits),
      converter, source_request, limits, start_function);
}

}  // namespace

std::optional<WholeSequenceConversion> BuildWholeSequenceConversion(
    const composer::Composer& original, const Hypothesis& hypothesis,
    const ConverterInterface& converter,
    const ConversionRequest& source_request) {
  return BuildWholeSequenceWithStart(original, hypothesis, converter,
                                     source_request,
                                     &ConverterInterface::StartConversion);
}

std::optional<WholeSequenceConversion> BuildWholeSequencePrediction(
    const composer::Composer& original, const Hypothesis& hypothesis,
    const ConverterInterface& converter,
    const ConversionRequest& source_request) {
  return BuildWholeSequenceWithStart(original, hypothesis, converter,
                                     source_request,
                                     &ConverterInterface::StartPrediction);
}

std::vector<WholeSequenceConversion> GenerateShadowConversions(
    const composer::Composer& original, const RomanInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits, IncrementalRomanCache* cache) {
  return GenerateShadowWithStart(original, context, converter, source_request,
                                 limits,
                                 &ConverterInterface::StartConversion, cache);
}

std::vector<WholeSequenceConversion> GenerateShadowPredictions(
    const composer::Composer& original, const RomanInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits, IncrementalRomanCache* cache) {
  return GenerateShadowWithStart(original, context, converter, source_request,
                                 limits,
                                 &ConverterInterface::StartPrediction, cache);
}

std::vector<WholeSequenceConversion> GenerateShadowKanaConversions(
    const composer::Composer& original, const KanaInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits) {
  return GenerateKanaShadowWithStart(
      original, context, converter, source_request, limits,
      &ConverterInterface::StartConversion);
}

std::vector<WholeSequenceConversion> GenerateShadowKanaPredictions(
    const composer::Composer& original, const KanaInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits) {
  return GenerateKanaShadowWithStart(
      original, context, converter, source_request, limits,
      &ConverterInterface::StartPrediction);
}

std::vector<WholeSequenceConversion> GenerateShadowKanaModeMismatchWithStart(
    const composer::Composer& original,
    const KanaModeMismatchInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits, const StartFunction start_function) {
  return GenerateShadowForHypotheses(
      original, GenerateKanaModeMismatchHypotheses(original, context, limits),
      converter, source_request, limits, start_function);
}

std::vector<WholeSequenceConversion> GenerateShadowKanaModeMismatch(
    const composer::Composer& original,
    const KanaModeMismatchInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits) {
  return GenerateShadowKanaModeMismatchWithStart(
      original, context, converter, source_request, limits,
      &ConverterInterface::StartConversion);
}

std::vector<WholeSequenceConversion> GenerateShadowKanaModeMismatchPredictions(
    const composer::Composer& original,
    const KanaModeMismatchInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits) {
  return GenerateShadowKanaModeMismatchWithStart(
      original, context, converter, source_request, limits,
      &ConverterInterface::StartPrediction);
}

}  // namespace mozc::typing_correction
