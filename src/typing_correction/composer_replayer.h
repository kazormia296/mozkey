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

#ifndef MOZC_TYPING_CORRECTION_COMPOSER_REPLAYER_H_
#define MOZC_TYPING_CORRECTION_COMPOSER_REPLAYER_H_

#include <optional>
#include <vector>

#include "absl/strings/string_view.h"
#include "composer/composer.h"
#include "typing_correction/incremental_cache.h"
#include "typing_correction/kana_typing_corrector.h"
#include "typing_correction/roman_typing_corrector.h"

namespace mozc::typing_correction {

// Replays the corrected raw sequence through the exact Table and Composer
// state held by `original`.  No fixed Romanji table is used here.
std::optional<composer::Composer> BuildCorrectedComposer(
    const composer::Composer& original, absl::string_view corrected_raw);

std::optional<composer::Composer> BuildCorrectedComposer(
    const composer::Composer& original, const Hypothesis& hypothesis);

// Generates gated raw hypotheses and fills their readings by replaying each
// one through the original Composer/Table state.  This is the boundary before
// converter-backed scoring; it does not mutate `original`.
std::vector<Hypothesis> GenerateRomanCorrectionHypotheses(
    const composer::Composer& original, const RomanInputGateContext& context,
    const Limits& limits = Limits(), IncrementalRomanCache* cache = nullptr);

std::vector<Hypothesis> GenerateKanaCorrectionHypotheses(
    const composer::Composer& original, const KanaInputGateContext& context,
    const Limits& limits = Limits());

// Replays physical JIS-kana key codes as Roman input while preserving the
// original key trace. This is a candidate-only mode-mismatch fallback.
std::optional<composer::Composer> BuildRomanModeReplayForKana(
    const composer::Composer& original);

std::vector<Hypothesis> GenerateKanaModeMismatchHypotheses(
    const composer::Composer& original,
    const KanaModeMismatchInputGateContext& context,
    const Limits& limits = Limits());

}  // namespace mozc::typing_correction

#endif  // MOZC_TYPING_CORRECTION_COMPOSER_REPLAYER_H_
