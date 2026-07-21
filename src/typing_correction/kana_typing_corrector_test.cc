// Copyright 2026 Grimodex contributors.

#include "typing_correction/kana_typing_corrector.h"
#include "typing_correction/generated_holdout_cases.h"
#include "typing_correction/generated_kana_rules.h"

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

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

std::vector<commands::KeyEvent> MakeJisEvents(
    const absl::string_view key_codes) {
  std::vector<commands::KeyEvent> events;
  for (const char key : std::string(key_codes)) {
    events.push_back(MakeEvent(key, JisKanaKeyString(key)));
  }
  return events;
}

TEST(KanaTypingCorrectorTest, GoldCasesCoverJisNeighborAndModifier) {
  ASSERT_EQ(kGeneratedKanaGoldCaseCount, 20);
  for (const KanaGoldCase& gold : KanaGoldCases()) {
    const int32_t typed_key_code = gold.typed_raw.front();
    const int32_t corrected_key_code = gold.corrected_raw.front();
    const std::vector<commands::KeyEvent> events = {
        MakeEvent(typed_key_code, JisKanaKeyString(typed_key_code))};
    const std::vector<Hypothesis> hypotheses =
        KanaTypingCorrector().Generate(events);
    const Hypothesis* hypothesis = FindByRaw(hypotheses, gold.corrected_raw);
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
  ASSERT_EQ(kGeneratedKanaNegativeCaseCount, 20);
  for (const KanaNegativeCase& test_case : KanaNegativeCases()) {
    std::vector<commands::KeyEvent> events;
    for (const char key : std::string(test_case.typed_raw)) {
      const absl::string_view kana = JisKanaKeyString(key);
      if (kana.empty()) {
        commands::KeyEvent event = MakeEvent(key, "?");
        events.push_back(std::move(event));
      } else {
        events.push_back(MakeEvent(key, kana));
      }
    }
    for (const Hypothesis& hypothesis : KanaTypingCorrector().Generate(events)) {
      EXPECT_FALSE(hypothesis.auto_applicable) << test_case.case_id;
    }
  }
}

TEST(KanaTypingCorrectorTest, IndependentHoldoutCasesCoverMultiKeyDuplicate) {
  ASSERT_EQ(kGeneratedKanaHoldoutCaseCount, 4);
  for (const KanaHoldoutCase& test_case : kGeneratedKanaHoldoutCases) {
    const std::vector<commands::KeyEvent> events =
        MakeJisEvents(test_case.typed_key_codes);
    const std::vector<Hypothesis> hypotheses =
        KanaTypingCorrector().Generate(events);
    const Hypothesis* hypothesis =
        FindByRaw(hypotheses, test_case.corrected_key_codes);
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
    ASSERT_EQ(hypothesis->corrected_key_events.size(), 1)
        << test_case.case_id;
    EXPECT_EQ(hypothesis->corrected_key_events.front().key_string(),
              test_case.corrected_key_strings)
        << test_case.case_id;
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
