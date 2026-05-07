// src/engine/contextual_candidate_reranker.cc

#include "engine/contextual_candidate_reranker.h"

#include <algorithm>
#include <cstddef>
#include <string>
#include <vector>

#include "absl/strings/string_view.h"
#include "base/util.h"
#include "converter/candidate.h"
#include "converter/segments.h"

namespace mozc {
namespace engine {
namespace {

using ::mozc::converter::Candidate;
using ::mozc::converter::Segment;
using ::mozc::converter::Segments;

bool ContainsScriptType(absl::string_view text, Util::ScriptType type) {
  ConstChar32Iterator iter(text);
  for (; !iter.Done(); iter.Next()) {
    if (Util::GetScriptType(iter.Get()) == type) {
      return true;
    }
  }
  return false;
}

bool ContainsKanji(absl::string_view text) {
  return ContainsScriptType(text, Util::KANJI);
}

bool ContainsKatakana(absl::string_view text) {
  return ContainsScriptType(text, Util::KATAKANA);
}

bool ContainsNumber(absl::string_view text) {
  return ContainsScriptType(text, Util::NUMBER);
}

bool IsPureHiragana(absl::string_view text) {
  if (text.empty()) {
    return false;
  }

  ConstChar32Iterator iter(text);
  for (; !iter.Done(); iter.Next()) {
    if (Util::GetScriptType(iter.Get()) != Util::HIRAGANA) {
      return false;
    }
  }
  return true;
}

bool IsAsciiOrSymbolLike(absl::string_view text) {
  if (text.empty()) {
    return false;
  }

  ConstChar32Iterator iter(text);
  for (; !iter.Done(); iter.Next()) {
    const char32_t c = iter.Get();
    const Util::ScriptType type = Util::GetScriptType(c);
    if (type == Util::ALPHABET || type == Util::NUMBER) {
      continue;
    }

    switch (c) {
      case U'-':
      case U'_':
      case U'#':
      case U'/':
      case U'.':
        continue;
      default:
        return false;
    }
  }

  return true;
}

bool IsParticleReading(absl::string_view key) {
  return key == "に" || key == "を" || key == "が" || key == "は" ||
         key == "へ" || key == "で" || key == "と" || key == "の" ||
         key == "も";
}

bool IsPlainParticleCandidate(const Segment& segment,
                              const Candidate& candidate) {
  if (!IsParticleReading(segment.key())) {
    return false;
  }

  return candidate.key == segment.key() &&
         candidate.content_key == segment.key() &&
         candidate.value == segment.key() &&
         candidate.content_value == segment.key();
}

bool IsPlainParticleSurfaceCandidate(const Segment& segment,
                                     const Candidate& candidate) {
  if (!IsParticleReading(segment.key())) {
    return false;
  }

  // Some duplicate candidates may have different POS IDs or internal costs.
  // For contextual particle reranking, the surface form is what matters.
  return candidate.value == segment.key() &&
         candidate.content_value == segment.key();
}

bool IsSuspiciousHomophoneForParticle(const Segment& segment,
                                      const Candidate& candidate) {
  if (!IsParticleReading(segment.key())) {
    return false;
  }

  // In a particle reading segment, anything whose surface is not the plain
  // particle is suspicious in ordinary left-context prose:
  //   に゛, ニ, ni, 二, 弐, 2, Ⅱ, 似, 荷, ...
  //
  // Do not try to classify by script type here. "に゛" can look hiragana-like
  // depending on Unicode/script classification, but it must not beat "に".
  return !IsPlainParticleSurfaceCandidate(segment, candidate);
}

bool HasHistorySegment(const Segments& segments) {
  return segments.segments_size() > 0 &&
         segments.segment(0).segment_type() == Segment::HISTORY &&
         segments.segment(0).candidates_size() > 0;
}

bool PreviousContextLooksNounLike(const Segments& segments,
                                  size_t conversion_index) {
  if (conversion_index == 0) {
    if (!HasHistorySegment(segments)) {
      return false;
    }

    const Segment& history = segments.segment(0);
    const Candidate& candidate = history.candidate(0);
    return !candidate.value.empty();
  }

  const Segment& previous = segments.conversion_segment(conversion_index - 1);
  if (previous.candidates_size() == 0) {
    return false;
  }

  const Candidate& previous_top = previous.candidate(0);
  if (previous_top.value.empty()) {
    return false;
  }

  // Coarse heuristic. This intentionally does not try to be a full POS parser.
  return ContainsKanji(previous_top.value) ||
         ContainsKatakana(previous_top.value) ||
         IsAsciiOrSymbolLike(previous_top.value);
}

bool NextContextLooksPhraseContinuation(const Segments& segments,
                                        size_t conversion_index) {
  if (conversion_index + 1 >= segments.conversion_segments_size()) {
    // Even without right context, noun + particle is often valid.
    // But for ambiguous particles such as "の" and "と", right context is
    // desirable. Keep this true for now; per-particle strength controls risk.
    return true;
  }

  const Segment& next = segments.conversion_segment(conversion_index + 1);
  if (next.candidates_size() == 0) {
    return false;
  }

  const Candidate& top = next.candidate(0);

  if (top.value.empty()) {
    return false;
  }

  if (ContainsKatakana(top.value)) {
    return true;
  }

  if (ContainsKanji(top.value)) {
    return true;
  }

  // Verb/adjective continuations are often represented partly in hiragana:
  //   に 送る
  //   を する
  //   が ある
  // Avoid being too strict here.
  if (IsPureHiragana(top.value)) {
    return true;
  }

  return false;
}

int ParticleBonus(absl::string_view key) {
  if (key == "に" || key == "を" || key == "が" || key == "へ" ||
      key == "で") {
    return 5000;
  }

  if (key == "は" || key == "も") {
    return 4000;
  }

  if (key == "と" || key == "の") {
    return 2500;
  }

  return 0;
}

struct CandidateScore {
  int index = 0;
  int adjusted_cost = 0;
};

void RerankParticleSegmentAfterContext(const Segments& segments,
                                       size_t conversion_index,
                                       Segment* segment) {
  if (segment == nullptr) {
    return;
  }

  if (!IsParticleReading(segment->key())) {
    return;
  }

  if (segment->candidates_size() <= 1) {
    return;
  }

  if (!PreviousContextLooksNounLike(segments, conversion_index)) {
    return;
  }

  if (!NextContextLooksPhraseContinuation(segments, conversion_index)) {
    return;
  }

  int plain_particle_index = -1;
  for (int i = 0; i < segment->candidates_size(); ++i) {
    if (IsPlainParticleCandidate(*segment, segment->candidate(i))) {
      plain_particle_index = i;
      break;
    }
  }

  if (plain_particle_index < 0) {
    return;
  }

  std::vector<CandidateScore> scores;
  scores.reserve(segment->candidates_size());

  const int bonus = ParticleBonus(segment->key());

  for (int i = 0; i < segment->candidates_size(); ++i) {
    const Candidate& candidate = segment->candidate(i);

    int adjusted_cost = candidate.cost;

    if (IsPlainParticleSurfaceCandidate(*segment, candidate)) {
      adjusted_cost -= bonus;
    } else if (IsSuspiciousHomophoneForParticle(*segment, candidate)) {
      adjusted_cost += bonus;
    }

    scores.push_back({i, adjusted_cost});
  }

  std::stable_sort(scores.begin(), scores.end(),
                   [](const CandidateScore& lhs, const CandidateScore& rhs) {
                     return lhs.adjusted_cost < rhs.adjusted_cost;
                   });

  // If the top candidate does not change, avoid unnecessary mutation.
  if (scores.empty() || scores[0].index == 0) {
    return;
  }

  std::vector<Candidate> reordered;
  reordered.reserve(segment->candidates_size());
  for (const CandidateScore& score : scores) {
    reordered.push_back(segment->candidate(score.index));
  }

  for (int i = 0; i < static_cast<int>(reordered.size()); ++i) {
    *segment->mutable_candidate(i) = reordered[i];
  }
}

}  // namespace

void ContextualCandidateReranker::Rerank(Segments* segments) const {
  if (segments == nullptr) {
    return;
  }

  for (size_t i = 0; i < segments->conversion_segments_size(); ++i) {
    Segment* segment = segments->mutable_conversion_segment(i);
    RerankParticleSegmentAfterContext(*segments, i, segment);
  }
}

}  // namespace engine
}  // namespace mozc
