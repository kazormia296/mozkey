// Copyright 2026 Grimodex contributors.

#include "typing_correction/typing_correction.h"

#include <vector>

#include "gtest/gtest.h"

namespace mozc::typing_correction {
namespace {

TEST(TypingCorrectionTest, GateRequiresAllSafetyConditions) {
  RomanInputGateContext context;
  context.feature_enabled = true;

  EXPECT_TRUE(IsEligibleForRomanCorrection(context, "kudasia"));

  context.feature_enabled = false;
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "kudasia"));
  context.feature_enabled = true;

  context.cursor_at_end = false;
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "kudasia"));
  context.cursor_at_end = true;

  context.secure_input = true;
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "kudasia"));
  context.secure_input = false;

  context.reverse_conversion = true;
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "kudasia"));
  context.reverse_conversion = false;

  context.ascii_input_mode = true;
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "kudasia"));
  context.ascii_input_mode = false;

  context.mixed_script = true;
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "kudasia"));
  context.mixed_script = false;

  context.url_like = true;
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "kudasia"));
  context.url_like = false;

  context.email_like = true;
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "kudasia"));
  context.email_like = false;

  context.path_like = true;
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "kudasia"));
}

TEST(TypingCorrectionTest, GateRejectsUnsupportedRawShapes) {
  RomanInputGateContext context;
  context.feature_enabled = true;

  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "abc"));
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "Kudasia"));
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "kuda1a"));
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "あいうえ"));

  context.max_raw_bytes = 6;
  EXPECT_FALSE(IsEligibleForRomanCorrection(context, "kudasia"));
}

TEST(TypingCorrectionTest, OperationNamesAreStable) {
  EXPECT_STREQ(OperationName(Operation::kAdjacentTranspose), "transpose");
  EXPECT_STREQ(OperationName(Operation::kMissingKeyInsertion),
               "missing_key_insertion");
  EXPECT_STREQ(OperationName(Operation::kInputModeReplay),
               "input_mode_replay");
}

TEST(TypingCorrectionTest, KanaGateRejectsModifierInsensitiveAndIncompleteTrace) {
  commands::KeyEvent event;
  event.set_key_code('5');
  event.set_key_string("え");
  const std::vector<commands::KeyEvent> events = {event};

  KanaInputGateContext context;
  context.feature_enabled = true;
  context.is_jis_kana_input = true;
  EXPECT_TRUE(IsEligibleForKanaCorrection(context, events, "5"));

  context.modifier_insensitive_conversion = true;
  EXPECT_FALSE(IsEligibleForKanaCorrection(context, events, "5"));
  context.modifier_insensitive_conversion = false;

  context.cursor_at_end = false;
  EXPECT_FALSE(IsEligibleForKanaCorrection(context, events, "5"));
  context.cursor_at_end = true;

  commands::KeyEvent incomplete;
  incomplete.set_key_code('5');
  const std::vector<commands::KeyEvent> incomplete_events = {incomplete};
  EXPECT_FALSE(
      IsEligibleForKanaCorrection(context, incomplete_events, "5"));

  commands::KeyEvent modified = event;
  modified.add_modifier_keys(commands::KeyEvent::SHIFT);
  const std::vector<commands::KeyEvent> modified_events = {modified};
  EXPECT_FALSE(IsEligibleForKanaCorrection(context, modified_events, "5"));
}

TEST(TypingCorrectionTest, KanaModeMismatchGateRejectsUnsafeTrace) {
  commands::KeyEvent event;
  event.set_key_code('t');
  event.set_key_string("か");
  const std::vector<commands::KeyEvent> events = {event};

  KanaModeMismatchInputGateContext context;
  context.feature_enabled = true;
  EXPECT_TRUE(IsEligibleForKanaModeMismatch(context, events, "か"));

  context.secure_input = true;
  EXPECT_FALSE(IsEligibleForKanaModeMismatch(context, events, "か"));
  context.secure_input = false;

  context.reverse_conversion = true;
  EXPECT_FALSE(IsEligibleForKanaModeMismatch(context, events, "か"));
  context.reverse_conversion = false;

  context.ascii_input_mode = true;
  EXPECT_FALSE(IsEligibleForKanaModeMismatch(context, events, "か"));
  context.ascii_input_mode = false;

  context.cursor_at_end = false;
  EXPECT_FALSE(IsEligibleForKanaModeMismatch(context, events, "か"));
  context.cursor_at_end = true;

  commands::KeyEvent modified = event;
  modified.add_modifier_keys(commands::KeyEvent::SHIFT);
  const std::vector<commands::KeyEvent> modified_events = {modified};
  EXPECT_FALSE(
      IsEligibleForKanaModeMismatch(context, modified_events, "か"));
}

}  // namespace
}  // namespace mozc::typing_correction
