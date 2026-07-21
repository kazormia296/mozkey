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

#ifndef MOZC_TYPING_CORRECTION_SHADOW_CONVERSION_H_
#define MOZC_TYPING_CORRECTION_SHADOW_CONVERSION_H_

#include <cstdint>
#include <optional>
#include <vector>

#include "absl/strings/string_view.h"
#include "composer/composer.h"
#include "converter/candidate.h"
#include "converter/converter_interface.h"
#include "request/conversion_request.h"
#include "typing_correction/incremental_cache.h"
#include "typing_correction/roman_typing_corrector.h"

namespace mozc::typing_correction {

// The correction penalty is intentionally small compared with a converter
// path cost, but large enough to make an otherwise equivalent correction lose
// to the identity result.  This is a ranking input, not a confidence model.
inline constexpr int32_t kCorrectionCostScale = 10;

struct WholeSequenceConversion {
  Hypothesis hypothesis;
  converter::Candidate candidate;
  std::vector<ExternalConversionSegment> learning_segments;
  int32_t converter_cost = 0;
  int32_t total_cost = 0;
  // Filled by EngineConverter after the candidate is appended to the source
  // segment.  Keeping this index beside the scratch result avoids using a
  // value-string lookup during commit, where duplicate surfaces are valid.
  int candidate_index = -1;
};

// Converts one already-replayed hypothesis using the same converter request
// context as the source input.  The returned candidate is a display/commit
// boundary: it contains all scratch conversion segments, while the original
// Segments owned by EngineConverter remain untouched.
std::optional<WholeSequenceConversion> BuildWholeSequenceConversion(
    const composer::Composer& original,
    const Hypothesis& hypothesis,
    const ConverterInterface& converter,
    const ConversionRequest& source_request);

// Prediction/suggestion counterpart of BuildWholeSequenceConversion.  It
// uses the request's prediction mode but keeps the same whole-sequence data
// and corrected-reading learning boundary.
std::optional<WholeSequenceConversion> BuildWholeSequencePrediction(
    const composer::Composer& original,
    const Hypothesis& hypothesis,
    const ConverterInterface& converter,
    const ConversionRequest& source_request);

// Generates and shadow-converts at most |limits.max_reading_hypotheses|
// distinct corrected readings.  The converter is called only for corrected
// hypotheses; callers are responsible for keeping the source conversion
// alive and for deciding whether a candidate is merely suggested or applied.
std::vector<WholeSequenceConversion> GenerateShadowConversions(
    const composer::Composer& original, const RomanInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits = Limits(), IncrementalRomanCache* cache = nullptr);

std::vector<WholeSequenceConversion> GenerateShadowPredictions(
    const composer::Composer& original, const RomanInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits = Limits(), IncrementalRomanCache* cache = nullptr);

std::vector<WholeSequenceConversion> GenerateShadowKanaConversions(
    const composer::Composer& original, const KanaInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits = Limits());

std::vector<WholeSequenceConversion> GenerateShadowKanaPredictions(
    const composer::Composer& original, const KanaInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits = Limits());

std::vector<WholeSequenceConversion> GenerateShadowKanaModeMismatch(
    const composer::Composer& original,
    const KanaModeMismatchInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits = Limits());

std::vector<WholeSequenceConversion> GenerateShadowKanaModeMismatchPredictions(
    const composer::Composer& original,
    const KanaModeMismatchInputGateContext& context,
    const ConverterInterface& converter, const ConversionRequest& source_request,
    const Limits& limits = Limits());

}  // namespace mozc::typing_correction

#endif  // MOZC_TYPING_CORRECTION_SHADOW_CONVERSION_H_
