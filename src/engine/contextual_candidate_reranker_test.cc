// Copyright 2010-2021, Google Inc.
// All rights reserved.
//
// Redistribution and use in source and binary forms, with or without
// modification, are permitted provided that the following conditions are
// met:
//
//     * Redistributions of source code must retain the above copyright
// notice, this list of conditions and the following disclaimer.
//     * Redistributions in binary form must reproduce the above
// copyright notice, this list of conditions and the following disclaimer
// in the documentation and/or other materials provided with the
// distribution.
//     * Neither the name of Google Inc. nor the names of its
// contributors may be used to endorse or promote products derived from
// this software without specific prior written permission.
//
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
// "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
// LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
// A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
// OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
// SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES INCLUDING, BUT NOT
// LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
// DATA, OR PROFITS; OR BUSINESS INTERRUPTION HOWEVER CAUSED AND ON ANY
// THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
// INCLUDING NEGLIGENCE OR OTHERWISE ARISING IN ANY WAY OUT OF THE USE
// OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

#include "engine/contextual_candidate_reranker.h"

#include <string>

#include "absl/strings/string_view.h"
#include "converter/candidate.h"
#include "converter/segments.h"
#include "testing/gunit.h"

namespace mozc {
namespace engine {
namespace {

using ::mozc::converter::Candidate;
using ::mozc::converter::Segment;
using ::mozc::converter::Segments;

Candidate* AddCandidate(Segment* segment, absl::string_view key,
                        absl::string_view value, int cost) {
  Candidate* candidate = segment->add_candidate();
  candidate->key = std::string(key);
  candidate->content_key = std::string(key);
  candidate->value = std::string(value);
  candidate->content_value = std::string(value);
  candidate->cost = cost;
  return candidate;
}

Segment* AddHistorySegment(Segments* segments, absl::string_view key,
                           absl::string_view value) {
  Segment* segment = segments->add_segment();
  segment->set_segment_type(Segment::HISTORY);
  segment->set_key(key);

  Candidate* candidate = segment->add_candidate();
  candidate->key = std::string(key);
  candidate->content_key = std::string(key);
  candidate->value = std::string(value);
  candidate->content_value = std::string(value);
  candidate->cost = 0;
  return segment;
}

Segment* AddConversionSegment(Segments* segments, absl::string_view key) {
  Segment* segment = segments->add_segment();
  segment->set_segment_type(Segment::FREE);
  segment->set_key(key);
  return segment;
}

void AddParticleSegmentWithBadTopCandidate(Segments* segments,
                                           absl::string_view particle_key,
                                           absl::string_view bad_value) {
  Segment* segment = AddConversionSegment(segments, particle_key);

  // Bad homophone candidate is initially on top.
  AddCandidate(segment, particle_key, bad_value, 5000);

  // Plain particle candidate is initially worse by cost.
  AddCandidate(segment, particle_key, particle_key, 9500);

  // Other noisy candidates.
  AddCandidate(segment, particle_key, "似", 7000);
  AddCandidate(segment, particle_key, "2", 5100);
}

void AddKatakanaNextSegment(Segments* segments, absl::string_view key,
                            absl::string_view value) {
  Segment* segment = AddConversionSegment(segments, key);
  AddCandidate(segment, key, value, 7000);
}

void ExpectParticlePromotedAfterHistory(absl::string_view particle_key,
                                        absl::string_view bad_value) {
  Segments segments;
  AddHistorySegment(&segments, "とうきょう", "東京");
  AddParticleSegmentWithBadTopCandidate(&segments, particle_key, bad_value);
  AddKatakanaNextSegment(&segments, "まーじ", "マージ");

  ASSERT_EQ(segments.conversion_segments_size(), 2);
  ASSERT_EQ(segments.conversion_segment(0).key(), particle_key);
  ASSERT_EQ(segments.conversion_segment(0).candidate(0).value, bad_value);

  ContextualCandidateReranker reranker;
  reranker.Rerank(&segments);

  ASSERT_EQ(segments.conversion_segments_size(), 2);
  EXPECT_EQ(segments.conversion_segment(0).key(), particle_key);
  EXPECT_EQ(segments.conversion_segment(0).candidate(0).value, particle_key);
}

TEST(ContextualCandidateRerankerTest, PromotesNiParticleAfterHistory) {
  ExpectParticlePromotedAfterHistory("に", "二");
}

TEST(ContextualCandidateRerankerTest, PromotesWoParticleAfterHistory) {
  ExpectParticlePromotedAfterHistory("を", "尾");
}

TEST(ContextualCandidateRerankerTest, PromotesGaParticleAfterHistory) {
  ExpectParticlePromotedAfterHistory("が", "画");
}

TEST(ContextualCandidateRerankerTest, PromotesHaParticleAfterHistory) {
  ExpectParticlePromotedAfterHistory("は", "歯");
}

TEST(ContextualCandidateRerankerTest, PromotesHeParticleAfterHistory) {
  ExpectParticlePromotedAfterHistory("へ", "辺");
}

TEST(ContextualCandidateRerankerTest, PromotesDeParticleAfterHistory) {
  ExpectParticlePromotedAfterHistory("で", "出");
}

TEST(ContextualCandidateRerankerTest, PromotesToParticleAfterHistory) {
  ExpectParticlePromotedAfterHistory("と", "戸");
}

TEST(ContextualCandidateRerankerTest, PromotesNoParticleAfterHistory) {
  ExpectParticlePromotedAfterHistory("の", "野");
}

TEST(ContextualCandidateRerankerTest, PromotesMoParticleAfterHistory) {
  ExpectParticlePromotedAfterHistory("も", "藻");
}

TEST(ContextualCandidateRerankerTest, DoesNotPromoteWithoutHistory) {
  Segments segments;
  AddParticleSegmentWithBadTopCandidate(&segments, "に", "二");
  AddKatakanaNextSegment(&segments, "まーじ", "マージ");

  ASSERT_EQ(segments.conversion_segments_size(), 2);
  ASSERT_EQ(segments.conversion_segment(0).candidate(0).value, "二");

  ContextualCandidateReranker reranker;
  reranker.Rerank(&segments);

  EXPECT_EQ(segments.conversion_segment(0).candidate(0).value, "二");
}

TEST(ContextualCandidateRerankerTest, PromotesAfterPreviousConversionSegment) {
  Segments segments;

  Segment* previous = AddConversionSegment(&segments, "とうきょう");
  AddCandidate(previous, "とうきょう", "東京", 4000);

  AddParticleSegmentWithBadTopCandidate(&segments, "に", "二");
  AddKatakanaNextSegment(&segments, "まーじ", "マージ");

  ASSERT_EQ(segments.conversion_segments_size(), 3);
  ASSERT_EQ(segments.conversion_segment(1).key(), "に");
  ASSERT_EQ(segments.conversion_segment(1).candidate(0).value, "二");

  ContextualCandidateReranker reranker;
  reranker.Rerank(&segments);

  EXPECT_EQ(segments.conversion_segment(1).candidate(0).value, "に");
}

TEST(ContextualCandidateRerankerTest, DoesNotTouchNonParticleSegment) {
  Segments segments;
  AddHistorySegment(&segments, "とうきょう", "東京");

  Segment* segment = AddConversionSegment(&segments, "まーじ");
  AddCandidate(segment, "まーじ", "マージ", 7000);
  AddCandidate(segment, "まーじ", "まーじ", 9000);

  ASSERT_EQ(segments.conversion_segments_size(), 1);
  ASSERT_EQ(segments.conversion_segment(0).candidate(0).value, "マージ");

  ContextualCandidateReranker reranker;
  reranker.Rerank(&segments);

  EXPECT_EQ(segments.conversion_segment(0).candidate(0).value, "マージ");
}

TEST(ContextualCandidateRerankerTest, DoesNotTouchParticleWithoutPlainCandidate) {
  Segments segments;
  AddHistorySegment(&segments, "とうきょう", "東京");

  Segment* segment = AddConversionSegment(&segments, "に");
  AddCandidate(segment, "に", "二", 5000);
  AddCandidate(segment, "に", "似", 7000);
  AddKatakanaNextSegment(&segments, "まーじ", "マージ");

  ASSERT_EQ(segments.conversion_segments_size(), 2);
  ASSERT_EQ(segments.conversion_segment(0).candidate(0).value, "二");

  ContextualCandidateReranker reranker;
  reranker.Rerank(&segments);

  EXPECT_EQ(segments.conversion_segment(0).candidate(0).value, "二");
}

TEST(ContextualCandidateRerankerTest, DoesNotTouchWhenParticleAlreadyTop) {
  Segments segments;
  AddHistorySegment(&segments, "とうきょう", "東京");

  Segment* segment = AddConversionSegment(&segments, "に");
  AddCandidate(segment, "に", "に", 5000);
  AddCandidate(segment, "に", "二", 9500);
  AddKatakanaNextSegment(&segments, "まーじ", "マージ");

  ASSERT_EQ(segments.conversion_segments_size(), 2);
  ASSERT_EQ(segments.conversion_segment(0).candidate(0).value, "に");

  ContextualCandidateReranker reranker;
  reranker.Rerank(&segments);

  EXPECT_EQ(segments.conversion_segment(0).candidate(0).value, "に");
}

TEST(ContextualCandidateRerankerTest,
     PromotesPlainParticleOverDakutenKanaAfterAsciiHistory) {
  Segments segments;
  AddHistorySegment(&segments, "main", "main");

  Segment* particle = AddConversionSegment(&segments, "に");

  // Regression case:
  // "に゛" may have a much lower raw cost than the plain particle "に".
  // Contextual reranking must still prefer the plain particle after noun-like
  // ASCII history.
  AddCandidate(particle, "に", "に゛", 4258);
  AddCandidate(particle, "に", "に", 9352);
  AddCandidate(particle, "に", "二", 4258);

  AddKatakanaNextSegment(&segments, "まーじ", "マージ");

  ASSERT_EQ(segments.conversion_segments_size(), 2);
  ASSERT_EQ(segments.conversion_segment(0).key(), "に");
  ASSERT_EQ(segments.conversion_segment(0).candidate(0).value, "に゛");

  ContextualCandidateReranker reranker;
  reranker.Rerank(&segments);

  EXPECT_EQ(segments.conversion_segment(0).candidate(0).value, "に");
}

}  // namespace
}  // namespace engine
}  // namespace mozc
