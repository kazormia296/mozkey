// Copyright 2026 Grimodex contributors.

#include "typing_correction/composer_replayer.h"
#include "typing_correction/generated_roman_rules.h"

#include <optional>
#include <memory>

#include "composer/table.h"
#include "gtest/gtest.h"
#include "protocol/commands.pb.h"
#include "protocol/config.pb.h"

namespace mozc::typing_correction {
namespace {

TEST(ComposerReplayerTest, UsesTheOriginalComposerTable) {
  auto table = std::make_shared<composer::Table>();
  table->AddRule("ku", "く", "");
  table->AddRule("da", "だ", "");
  table->AddRule("sa", "さ", "");
  table->AddRule("si", "し", "");
  table->AddRule("i", "い", "");
  table->AddRule("a", "あ", "");
  composer::Composer original(table, commands::Request(), config::Config());
  original.InsertCharacter("kudasai");

  const std::optional<composer::Composer> replay =
      BuildCorrectedComposer(original, "kudasia");
  ASSERT_TRUE(replay.has_value());
  EXPECT_EQ(replay->GetRawString(), "kudasia");
  EXPECT_NE(replay->GetQueryForConversion(),
            original.GetQueryForConversion());
}

TEST(ComposerReplayerTest, RejectsIdentityAndUnresolvedReplay) {
  auto table = std::make_shared<composer::Table>();
  table->AddRule("ku", "く", "");
  table->AddRule("da", "だ", "");
  table->AddRule("sa", "さ", "");
  table->AddRule("i", "い", "");
  composer::Composer original(table, commands::Request(), config::Config());
  original.InsertCharacter("kudasai");

  EXPECT_FALSE(BuildCorrectedComposer(original, "kudasai").has_value());
  EXPECT_FALSE(BuildCorrectedComposer(original, "kudasaiq").has_value());
}

TEST(ComposerReplayerTest, PreservesInputFieldState) {
  auto table = std::make_shared<composer::Table>();
  table->AddRule("ku", "く", "");
  table->AddRule("da", "だ", "");
  table->AddRule("sa", "さ", "");
  table->AddRule("si", "し", "");
  table->AddRule("i", "い", "");
  table->AddRule("a", "あ", "");
  composer::Composer original(table, commands::Request(), config::Config());
  original.SetInputFieldType(commands::Context::PASSWORD);
  original.InsertCharacter("kudasai");

  const std::optional<composer::Composer> replay =
      BuildCorrectedComposer(original, "kudasia");
  ASSERT_TRUE(replay.has_value());
  EXPECT_EQ(replay->GetInputFieldType(), commands::Context::PASSWORD);
}

TEST(ComposerReplayerTest, GeneratesReadingsThroughTheCurrentTable) {
  auto table = std::make_shared<composer::Table>();
  table->AddRule("ku", "く", "");
  table->AddRule("da", "だ", "");
  table->AddRule("sa", "さ", "");
  table->AddRule("si", "し", "");
  table->AddRule("i", "い", "");
  table->AddRule("a", "あ", "");
  composer::Composer original(table, commands::Request(), config::Config());
  original.InsertCharacter("kudasia");

  RomanInputGateContext context;
  context.feature_enabled = true;
  const std::vector<Hypothesis> hypotheses =
      GenerateRomanCorrectionHypotheses(original, context);

  const Hypothesis* corrected = nullptr;
  for (const Hypothesis& hypothesis : hypotheses) {
    if (hypothesis.corrected_raw == "kudasai") {
      corrected = &hypothesis;
      break;
    }
  }
  ASSERT_NE(corrected, nullptr);
  EXPECT_EQ(corrected->original_reading, "くだしあ");
  EXPECT_EQ(corrected->corrected_reading, "ください");
  EXPECT_TRUE(corrected->auto_applicable);
}

TEST(ComposerReplayerTest, CustomRomanTableDisablesCorpusAutoRules) {
  auto table = std::make_shared<composer::Table>();
  table->AddRule("ku", "く", "");
  table->AddRule("da", "だ", "");
  table->AddRule("sa", "さ", "");
  table->AddRule("si", "し", "");
  table->AddRule("i", "い", "");
  composer::Composer original(table, commands::Request(), config::Config());
  original.InsertCharacter("kudasia");

  RomanInputGateContext context;
  context.feature_enabled = true;
  context.default_roman_table = false;
  const std::vector<Hypothesis> hypotheses =
      GenerateRomanCorrectionHypotheses(original, context);
  for (const Hypothesis& hypothesis : hypotheses) {
    EXPECT_NE(hypothesis.edits.front().rule_id, "R000002");
  }
}

TEST(ComposerReplayerTest, RomanGoldCasesMatchRawReadingAndPolicy) {
  auto table = std::make_shared<composer::Table>();
  commands::Request request;
  config::Config config;
  ASSERT_TRUE(table->InitializeWithRequestAndConfig(request, config));
  composer::Composer original(table, request, config);

  RomanInputGateContext context;
  context.feature_enabled = true;
  for (const RomanGoldCase& test_case : kGeneratedRomanGoldCases) {
    original.EditErase();
    original.InsertCharacter(std::string(test_case.typed_raw));
    const std::vector<Hypothesis> hypotheses =
        GenerateRomanCorrectionHypotheses(original, context);
    const Hypothesis* corrected = nullptr;
    for (const Hypothesis& hypothesis : hypotheses) {
      if (hypothesis.corrected_raw == test_case.corrected_raw) {
        corrected = &hypothesis;
        break;
      }
    }
    ASSERT_NE(corrected, nullptr) << test_case.case_id;
    EXPECT_EQ(corrected->corrected_reading, test_case.expected_reading)
        << test_case.case_id;
    EXPECT_EQ(corrected->edits.front().operation, test_case.operation)
        << test_case.case_id;
    EXPECT_EQ(corrected->auto_applicable, test_case.auto_applicable)
        << test_case.case_id;
  }
}

TEST(ComposerReplayerTest, GatePreventsRawGenerationBeforeReplay) {
  auto table = std::make_shared<composer::Table>();
  table->AddRule("ka", "か", "");
  composer::Composer original(table, commands::Request(), config::Config());
  original.InsertCharacter("kana");

  RomanInputGateContext context;
  EXPECT_TRUE(GenerateRomanCorrectionHypotheses(original, context).empty());
  context.feature_enabled = true;
  original.MoveCursorLeft();
  EXPECT_TRUE(GenerateRomanCorrectionHypotheses(original, context).empty());
}

TEST(ComposerReplayerTest, ReplaysJisKanaKeyCodeAndKeyStringTogether) {
  auto table = std::make_shared<composer::Table>();
  commands::Request request;
  config::Config config;
  config.set_preedit_method(config::Config::KANA);
  table->InitializeWithRequestAndConfig(request, config);
  composer::Composer original(table, request, config);

  commands::KeyEvent event;
  event.set_key_code('5');
  event.set_key_string("え");
  ASSERT_TRUE(original.InsertCharacterKeyEvent(event));
  ASSERT_EQ(original.GetKeyEventTrace().size(), 1);

  KanaInputGateContext context;
  context.feature_enabled = true;
  context.is_jis_kana_input = true;
  const std::vector<Hypothesis> hypotheses =
      GenerateKanaCorrectionHypotheses(original, context);
  const Hypothesis* corrected = nullptr;
  for (const Hypothesis& hypothesis : hypotheses) {
    if (hypothesis.corrected_raw == "4") {
      corrected = &hypothesis;
      break;
    }
  }
  ASSERT_NE(corrected, nullptr);
  EXPECT_EQ(corrected->original_raw, "5");
  EXPECT_EQ(corrected->original_reading, "え");
  EXPECT_EQ(corrected->corrected_reading, "う");

  const std::optional<composer::Composer> replay =
      BuildCorrectedComposer(original, *corrected);
  ASSERT_TRUE(replay.has_value());
  EXPECT_EQ(replay->GetRawString(), "4");
  EXPECT_EQ(replay->GetQueryForConversion(), "う");
}

TEST(ComposerReplayerTest, ReplaysKanaPhysicalKeysAsRomanOnModeMismatch) {
  auto table = std::make_shared<composer::Table>();
  commands::Request request;
  config::Config config;
  config.set_preedit_method(config::Config::ROMAN);
  table->InitializeWithRequestAndConfig(request, config);
  composer::Composer original(table, request, config);

  commands::KeyEvent kana_t;
  kana_t.set_key_code('t');
  kana_t.set_key_string("か");
  commands::KeyEvent kana_u;
  kana_u.set_key_code('u');
  kana_u.set_key_string("な");
  ASSERT_TRUE(original.InsertCharacterKeyEvent(kana_t));
  ASSERT_TRUE(original.InsertCharacterKeyEvent(kana_u));
  ASSERT_EQ(original.GetQueryForConversion(), "かな");

  const std::optional<composer::Composer> replay =
      BuildRomanModeReplayForKana(original);
  ASSERT_TRUE(replay.has_value());
  EXPECT_NE(replay->GetQueryForConversion(), "かな");

  KanaModeMismatchInputGateContext context;
  context.feature_enabled = true;
  const std::vector<Hypothesis> hypotheses =
      GenerateKanaModeMismatchHypotheses(original, context);
  ASSERT_EQ(hypotheses.size(), 1);
  EXPECT_EQ(hypotheses.front().edits.front().operation,
            Operation::kInputModeReplay);
  EXPECT_FALSE(hypotheses.front().auto_applicable);
}

}  // namespace
}  // namespace mozc::typing_correction
