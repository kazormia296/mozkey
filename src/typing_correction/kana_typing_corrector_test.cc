// Copyright 2026 Grimodex contributors.

#include "typing_correction/kana_typing_corrector.h"
#include "typing_correction/generated_holdout_cases.h"
#include "typing_correction/generated_kana_rules.h"

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

#include "base/util.h"
#include "gtest/gtest.h"

namespace mozc::typing_correction {
namespace {

commands::KeyEvent MakeEvent(const int key_code, const absl::string_view key) {
  commands::KeyEvent event;
  event.set_key_code(key_code);
  event.set_key_string(std::string(key));
  return event;
}

const Hypothesis* FindByRaw(const std::vector<Hypothesis>& hypotheses,
                            const absl::string_view raw) {
  for (const Hypothesis& hypothesis : hypotheses) {
    if (hypothesis.corrected_raw == raw) {
      return &hypothesis;
    }
  }
  return nullptr;
}

const Hypothesis* FindByTrace(const std::vector<Hypothesis>& hypotheses,
                              const absl::string_view key_codes,
                              const absl::string_view key_strings) {
  for (const Hypothesis& hypothesis : hypotheses) {
    if (hypothesis.corrected_key_events.size() != key_codes.size()) {
      continue;
    }
    bool matches = true;
    for (size_t index = 0; index < key_codes.size(); ++index) {
      const commands::KeyEvent& event = hypothesis.corrected_key_events[index];
      if (event.key_code() != key_codes[index] ||
          event.key_string() != Util::Utf8SubString(key_strings, index, 1)) {
        matches = false;
        break;
      }
    }
    if (matches) {
      return &hypothesis;
    }
  }
  return nullptr;
}

std::vector<commands::KeyEvent> MakeTraceEvents(
    const absl::string_view key_codes, const absl::string_view key_strings) {
  std::vector<commands::KeyEvent> events;
  for (size_t index = 0; index < key_codes.size(); ++index) {
    events.push_back(MakeEvent(
        key_codes[index], Util::Utf8SubString(key_strings, index, 1)));
  }
  return events;
}

TEST(KanaTypingCorrectorTest, GoldCasesCoverJisNeighborAndModifier) {
  ASSERT_EQ(kGeneratedKanaGoldCaseCount,
            kTypingCorrectionKanaGoldTargetCount);
  for (const KanaGoldCase& gold : KanaGoldCases()) {
    const int32_t typed_key_code = gold.typed_raw.front();
    const int32_t corrected_key_code = gold.corrected_raw.front();
    const std::vector<commands::KeyEvent> events = {
        MakeEvent(typed_key_code, gold.typed_key_string)};
    const std::vector<Hypothesis> hypotheses =
        KanaTypingCorrector().Generate(events);
    const Hypothesis* hypothesis =
        FindByTrace(hypotheses, gold.corrected_raw, gold.corrected_key_string);
    ASSERT_NE(hypothesis, nullptr) << gold.case_id;
    ASSERT_EQ(hypothesis->edits.size(), 1);
    EXPECT_EQ(hypothesis->edits.front().operation, gold.operation)
        << gold.case_id;
    EXPECT_EQ(hypothesis->auto_applicable, gold.auto_applicable)
        << gold.case_id;
    ASSERT_EQ(hypothesis->corrected_key_events.size(), 1);
    EXPECT_EQ(hypothesis->corrected_key_events.front().key_code(),
              corrected_key_code)
        << gold.case_id;
    EXPECT_EQ(hypothesis->corrected_key_events.front().key_string(),
              gold.corrected_key_string)
        << gold.case_id;
  }
}

TEST(KanaTypingCorrectorTest, SamePhysicalKeyCanStillBeAKeyStringCorrection) {
  const std::vector<commands::KeyEvent> events = {MakeEvent('3', "あ")};
  const std::vector<Hypothesis> hypotheses = KanaTypingCorrector().Generate(events);
  const Hypothesis* hypothesis = FindByRaw(hypotheses, "3");
  ASSERT_NE(hypothesis, nullptr);
  EXPECT_EQ(hypothesis->edits.front().operation, Operation::kKanaModifier);
  EXPECT_EQ(hypothesis->corrected_key_events.front().key_string(), "ぁ");
  EXPECT_FALSE(hypothesis->auto_applicable);
}

TEST(KanaTypingCorrectorTest, DakutenAndHandakutenStayModifierSensitive) {
  const std::vector<commands::KeyEvent> events = {MakeEvent('@', "゛")};
  const std::vector<Hypothesis> hypotheses = KanaTypingCorrector().Generate(events);
  const Hypothesis* hypothesis = FindByRaw(hypotheses, "[");
  ASSERT_NE(hypothesis, nullptr);
  EXPECT_EQ(hypothesis->edits.front().operation, Operation::kKanaModifier);
  EXPECT_EQ(hypothesis->corrected_key_events.front().key_string(), "゜");
  EXPECT_FALSE(hypothesis->auto_applicable);
}

TEST(KanaTypingCorrectorTest, NegativeCasesNeverAutoApply) {
  ASSERT_EQ(kGeneratedKanaNegativeCaseCount,
            kTypingCorrectionKanaNegativeTargetCount);
  for (const KanaNegativeCase& test_case : KanaNegativeCases()) {
    const std::vector<commands::KeyEvent> events =
        MakeTraceEvents(test_case.typed_raw, test_case.typed_key_string);
    for (const Hypothesis& hypothesis : KanaTypingCorrector().Generate(events)) {
      EXPECT_FALSE(hypothesis.auto_applicable) << test_case.case_id;
    }
  }
}

TEST(KanaTypingCorrectorTest, IndependentHoldoutCasesCoverMultipleOperations) {
  ASSERT_EQ(kGeneratedKanaHoldoutCaseCount,
            kTypingCorrectionKanaHoldoutTargetCount);
  for (const KanaHoldoutCase& test_case : kGeneratedKanaHoldoutCases) {
    const std::vector<commands::KeyEvent> events =
        MakeTraceEvents(test_case.typed_key_codes, test_case.typed_key_strings);
    const std::vector<Hypothesis> hypotheses =
        KanaTypingCorrector().Generate(events);
    const Hypothesis* hypothesis = FindByTrace(
        hypotheses, test_case.corrected_key_codes,
        test_case.corrected_key_strings);
    ASSERT_NE(hypothesis, nullptr) << test_case.case_id;
    ASSERT_EQ(hypothesis->edits.size(), 1) << test_case.case_id;
    EXPECT_EQ(hypothesis->edits.front().operation, test_case.operation)
        << test_case.case_id;
    EXPECT_FALSE(hypothesis->auto_applicable) << test_case.case_id;
    ASSERT_EQ(hypothesis->corrected_key_events.size(),
              test_case.corrected_key_codes.size())
        << test_case.case_id;
    for (size_t index = 0; index < test_case.corrected_key_codes.size();
         ++index) {
      EXPECT_EQ(hypothesis->corrected_key_events[index].key_code(),
                test_case.corrected_key_codes[index])
          << test_case.case_id;
    }
    for (size_t index = 0; index < test_case.corrected_key_codes.size();
         ++index) {
      EXPECT_EQ(hypothesis->corrected_key_events[index].key_string(),
                Util::Utf8SubString(test_case.corrected_key_strings, index, 1))
          << test_case.case_id;
    }
  }
}

TEST(KanaTypingCorrectorTest, CandidateAndEditBudgetsAreBounded) {
  std::vector<commands::KeyEvent> events;
  for (const char key : std::string("qwertyuiopasdfgh")) {
    events.push_back(MakeEvent(key, JisKanaKeyString(key)));
  }
  Limits limits;
  limits.max_raw_hypotheses = 3;
  EXPECT_LE(KanaTypingCorrector().Generate(events, limits).size(), 3);
  limits.max_edits = 2;
  EXPECT_TRUE(KanaTypingCorrector().Generate(events, limits).empty());
}

}  // namespace
}  // namespace mozc::typing_correction
