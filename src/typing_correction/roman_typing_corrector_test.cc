// Copyright 2026 Grimodex contributors.

#include "typing_correction/roman_typing_corrector.h"
#include "typing_correction/generated_holdout_cases.h"
#include "typing_correction/generated_roman_rules.h"

#include <cstdint>
#include <string>
#include <unordered_set>
#include <vector>

#include "gtest/gtest.h"

namespace mozc::typing_correction {
namespace {

const Hypothesis* FindCorrection(const std::vector<Hypothesis>& hypotheses,
                                 const std::string& corrected_raw) {
  for (const Hypothesis& hypothesis : hypotheses) {
    if (hypothesis.corrected_raw == corrected_raw) {
      return &hypothesis;
    }
  }
  return nullptr;
}

TEST(RomanTypingCorrectorTest, GoldCasesAreCovered) {
  const RomanTypingCorrector corrector;
  ASSERT_EQ(kGeneratedRomanGoldCaseCount,
            kTypingCorrectionRomanGoldTargetCount);
  for (const RomanGoldCase& test_case : kGeneratedRomanGoldCases) {
    const std::vector<Hypothesis> hypotheses =
        corrector.Generate(test_case.typed_raw);
    const Hypothesis* hypothesis =
        FindCorrection(hypotheses, std::string(test_case.corrected_raw));
    ASSERT_NE(hypothesis, nullptr) << test_case.case_id;
    ASSERT_EQ(hypothesis->edits.size(), 1);
    EXPECT_EQ(hypothesis->edits.front().operation, test_case.operation);
    EXPECT_EQ(hypothesis->auto_applicable, test_case.auto_applicable);
  }
}

TEST(RomanTypingCorrectorTest, NegativeCorpusHasNoAutomaticCorrections) {
  const RomanTypingCorrector corrector;
  ASSERT_EQ(kGeneratedRomanNegativeCaseCount,
            kTypingCorrectionRomanNegativeTargetCount);
  for (const RomanNegativeCase& test_case : kGeneratedRomanNegativeCases) {
    for (const Hypothesis& hypothesis : corrector.Generate(test_case.typed_raw)) {
      EXPECT_FALSE(hypothesis.auto_applicable) << test_case.case_id;
    }
  }
}

TEST(RomanTypingCorrectorTest, IndependentHoldoutCasesRemainCandidateOnly) {
  const RomanTypingCorrector corrector;
  ASSERT_EQ(kGeneratedRomanHoldoutCaseCount,
            kTypingCorrectionRomanHoldoutTargetCount);
  for (const RomanHoldoutCase& test_case : kGeneratedRomanHoldoutCases) {
    const std::vector<Hypothesis> hypotheses =
        corrector.Generate(test_case.typed_raw);
    const Hypothesis* hypothesis =
        FindCorrection(hypotheses, std::string(test_case.corrected_raw));
    ASSERT_NE(hypothesis, nullptr) << test_case.case_id;
    ASSERT_EQ(hypothesis->edits.size(), 1) << test_case.case_id;
    EXPECT_EQ(hypothesis->edits.front().operation, test_case.operation)
        << test_case.case_id;
    EXPECT_FALSE(hypothesis->auto_applicable) << test_case.case_id;
  }
}

TEST(RomanTypingCorrectorTest, GenericOperationsRemainCandidateOnly) {
  const RomanTypingCorrector corrector;

  const std::vector<Hypothesis> linux = corrector.Generate("linux");
  EXPECT_FALSE(linux.empty());
  for (const Hypothesis& hypothesis : linux) {
    EXPECT_FALSE(hypothesis.auto_applicable);
  }

  const std::vector<Hypothesis> www = corrector.Generate("www");
  EXPECT_FALSE(www.empty());
  for (const Hypothesis& hypothesis : www) {
    EXPECT_FALSE(hypothesis.auto_applicable);
  }

  const std::vector<Hypothesis> short_input = corrector.Generate("li");
  const Hypothesis* qwerty = FindCorrection(short_input, "ki");
  ASSERT_NE(qwerty, nullptr);
  EXPECT_EQ(qwerty->edits.front().operation,
            Operation::kNeighborSubstitution);
  EXPECT_FALSE(qwerty->auto_applicable);
}

TEST(RomanTypingCorrectorTest, DuplicateRawHypothesesPreferTrustedRule) {
  const std::vector<Hypothesis> hypotheses =
      RomanTypingCorrector().Generate("onegia");
  size_t matching_count = 0;
  for (const Hypothesis& hypothesis : hypotheses) {
    if (hypothesis.corrected_raw == "onegai") {
      ++matching_count;
      EXPECT_TRUE(hypothesis.auto_applicable);
      EXPECT_EQ(hypothesis.edits.front().rule_id, "R000001");
    }
  }
  EXPECT_EQ(matching_count, 1);
}

TEST(RomanTypingCorrectorTest, LimitsBoundCandidatesAndCost) {
  const RomanTypingCorrector corrector;

  Limits limits;
  limits.max_raw_hypotheses = 2;
  limits.max_edit_cost = 100;
  const std::vector<Hypothesis> hypotheses =
      corrector.Generate("kudasia", limits);
  EXPECT_LE(hypotheses.size(), 2);
  for (const Hypothesis& hypothesis : hypotheses) {
    EXPECT_LE(hypothesis.edit_cost, 100);
    EXPECT_EQ(hypothesis.edits.size(), 1);
  }

  limits.max_edits = 0;
  EXPECT_TRUE(corrector.Generate("kudasia", limits).empty());

  limits.max_edits = 2;
  EXPECT_TRUE(corrector.Generate("kudasia", limits).empty());
}

TEST(RomanTypingCorrectorTest, RulesExposeStableCorpusGeneratedData) {
  const absl::Span<const RomanRule> rules = DefaultRomanRules();
  ASSERT_GE(rules.size(), 8);
  EXPECT_EQ(rules.front().rule_id, "R000001");
  EXPECT_EQ(rules.front().wrong, "onegia");
  EXPECT_EQ(rules.front().corrected, "onegai");
  EXPECT_TRUE(rules.front().whole_input_only);
}

TEST(RomanTypingCorrectorTest, FuzzLikeAsciiInputsStayBounded) {
  const RomanTypingCorrector corrector;
  uint32_t state = 0x4d6f7a6b;
  for (size_t iteration = 0; iteration < 2000; ++iteration) {
    state = state * 1664525u + 1013904223u;
    const size_t length = 1 + (state % 64);
    std::string raw;
    raw.reserve(length);
    for (size_t index = 0; index < length; ++index) {
      state = state * 1664525u + 1013904223u;
      raw.push_back(static_cast<char>('a' + (state % 26)));
    }

    const std::vector<Hypothesis> hypotheses = corrector.Generate(raw);
    EXPECT_LE(hypotheses.size(), 16);
    std::unordered_set<std::string> corrected_raws;
    for (const Hypothesis& hypothesis : hypotheses) {
      EXPECT_TRUE(corrected_raws.insert(hypothesis.corrected_raw).second);
      EXPECT_EQ(hypothesis.original_raw, raw);
      EXPECT_NE(hypothesis.corrected_raw, raw);
      EXPECT_EQ(hypothesis.edits.size(), 1);
      EXPECT_LE(hypothesis.edit_cost, 300);
      for (const char character : hypothesis.corrected_raw) {
        EXPECT_GE(character, 'a');
        EXPECT_LE(character, 'z');
      }
    }
  }
}

}  // namespace
}  // namespace mozc::typing_correction
