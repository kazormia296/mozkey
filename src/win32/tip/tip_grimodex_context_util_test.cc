// Copyright 2026 The Mozkey Authors

#include "win32/tip/tip_grimodex_context_util.h"

#include <cstdint>
#include <optional>
#include <string>

#include "grimodex/client_context.h"
#include "protocol/commands.pb.h"
#include "testing/gunit.h"
#include "win32/tip/tip_input_mode_manager.h"
#include "win32/tip/tip_thread_context.h"

namespace mozc::win32::tsf {
namespace {

constexpr wchar_t kHighSurrogate = static_cast<wchar_t>(0xD83D);
constexpr wchar_t kLowSurrogate = static_cast<wchar_t>(0xDE00);

TEST(TipGrimodexContextUtilTest, LimitsPrecedingTextToClosestTwentyUtf16Units) {
  const std::wstring text = L"0123456789012345678901234";
  EXPECT_EQ(LimitTsfPrecedingText(text), L"56789012345678901234");
  EXPECT_EQ(LimitTsfPrecedingText(L"short"), L"short");
}

TEST(TipGrimodexContextUtilTest, LimitsFollowingTextToClosestTwentyUtf16Units) {
  const std::wstring text = L"0123456789012345678901234";
  EXPECT_EQ(LimitTsfFollowingText(text), L"01234567890123456789");
  EXPECT_EQ(LimitTsfFollowingText(L"short"), L"short");
}

TEST(TipGrimodexContextUtilTest, DoesNotSplitPrecedingSurrogatePair) {
  std::wstring text;
  text.push_back(kHighSurrogate);
  text.push_back(kLowSurrogate);
  text.append(19, L'x');

  const std::wstring limited = LimitTsfPrecedingText(text);
  EXPECT_EQ(limited, std::wstring(19, L'x'));
  ASSERT_FALSE(limited.empty());
  EXPECT_NE(static_cast<uint16_t>(limited.front()),
            static_cast<uint16_t>(kLowSurrogate));
}

TEST(TipGrimodexContextUtilTest, DoesNotSplitFollowingSurrogatePair) {
  std::wstring text(19, L'x');
  text.push_back(kHighSurrogate);
  text.push_back(kLowSurrogate);

  const std::wstring limited = LimitTsfFollowingText(text);
  EXPECT_EQ(limited, std::wstring(19, L'x'));
  ASSERT_FALSE(limited.empty());
  EXPECT_NE(static_cast<uint16_t>(limited.back()),
            static_cast<uint16_t>(kHighSurrogate));
}

TEST(TipGrimodexContextUtilTest, KeepsCompleteSurrogatePairWithinLimit) {
  std::wstring text(18, L'x');
  text.push_back(kHighSurrogate);
  text.push_back(kLowSurrogate);

  EXPECT_EQ(LimitTsfPrecedingText(text), text);
  EXPECT_EQ(LimitTsfFollowingText(text), text);
}

TEST(TipGrimodexContextUtilTest, FocusSnapshotRequiresEpochAndRevision) {
  TipThreadContext thread_context;
  const TsfFocusSnapshot snapshot =
      CaptureTsfFocusSnapshot(&thread_context);
  EXPECT_TRUE(IsTsfFocusSnapshotCurrent(&thread_context, snapshot));

  TsfFocusSnapshot wrong_epoch = snapshot;
  ++wrong_epoch.focus_epoch;
  EXPECT_FALSE(IsTsfFocusSnapshotCurrent(&thread_context, wrong_epoch));
  TsfFocusSnapshot wrong_revision = snapshot;
  ++wrong_revision.focus_revision;
  EXPECT_FALSE(IsTsfFocusSnapshotCurrent(&thread_context, wrong_revision));

  thread_context.IncrementFocusRevision();
  EXPECT_FALSE(IsTsfFocusSnapshotCurrent(&thread_context, snapshot));
  EXPECT_FALSE(IsTsfFocusSnapshotCurrent(nullptr, snapshot));
  EXPECT_FALSE(IsTsfFocusSnapshotCurrent(&thread_context, {}));
}

TEST(TipGrimodexContextUtilTest, StableProvidersPopulateNonSecureContext) {
  TipThreadContext thread_context;
  const commands::Context context = BuildTsfMozcContextFromProviders(
      &thread_context, /*include_surrounding_text=*/true,
      [] { return std::string("word"); },
      []() -> std::optional<grimodex::SurroundingText> {
        return grimodex::SurroundingText{
            .preceding_text = "before",
            .following_text = "after",
        };
      });

  ASSERT_TRUE(context.has_grimodex());
  EXPECT_EQ(context.grimodex().program(), "word");
  EXPECT_FALSE(context.grimodex().secure_input());
  EXPECT_EQ(context.preceding_text(), "before");
  EXPECT_EQ(context.following_text(), "after");
}

TEST(TipGrimodexContextUtilTest,
     ProgramProviderFocusChangeRebuildsFailClosedContext) {
  TipThreadContext thread_context;
  bool surrounding_text_provider_called = false;
  const commands::Context context = BuildTsfMozcContextFromProviders(
      &thread_context, /*include_surrounding_text=*/true,
      [&] {
        thread_context.IncrementFocusRevision();
        return std::string("stale-program");
      },
      [&]() -> std::optional<grimodex::SurroundingText> {
        surrounding_text_provider_called = true;
        return grimodex::SurroundingText{
            .preceding_text = "must-not-be-read",
            .following_text = "must-not-be-read",
        };
      });

  ASSERT_TRUE(context.has_grimodex());
  EXPECT_TRUE(context.grimodex().secure_input());
  EXPECT_TRUE(context.grimodex().program().empty());
  EXPECT_EQ(context.input_field_type(), commands::Context::PASSWORD);
  EXPECT_FALSE(context.has_preceding_text());
  EXPECT_FALSE(context.has_following_text());
  EXPECT_FALSE(surrounding_text_provider_called);
}

TEST(TipGrimodexContextUtilTest,
     SurroundingTextFocusChangeRebuildsFailClosedContext) {
  TipThreadContext thread_context;
  const commands::Context context = BuildTsfMozcContextFromProviders(
      &thread_context, /*include_surrounding_text=*/true,
      [] { return std::string("word"); },
      [&]() -> std::optional<grimodex::SurroundingText> {
        thread_context.IncrementFocusRevision();
        return grimodex::SurroundingText{
            .preceding_text = "secret-before",
            .following_text = "secret-after",
        };
      });

  ASSERT_TRUE(context.has_grimodex());
  EXPECT_TRUE(context.grimodex().secure_input());
  EXPECT_EQ(context.input_field_type(), commands::Context::PASSWORD);
  EXPECT_FALSE(context.has_preceding_text());
  EXPECT_FALSE(context.has_following_text());
}

TEST(TipGrimodexContextUtilTest,
     SurroundingTextSecureTransitionRebuildsFailClosedContext) {
  TipThreadContext thread_context;
  const commands::Context context = BuildTsfMozcContextFromProviders(
      &thread_context, /*include_surrounding_text=*/true,
      [] { return std::string("word"); },
      [&]() -> std::optional<grimodex::SurroundingText> {
        thread_context.GetInputModeManager()->OnInputScopeUnresolved();
        return grimodex::SurroundingText{
            .preceding_text = "secret-before",
            .following_text = "secret-after",
        };
      });

  ASSERT_TRUE(context.has_grimodex());
  EXPECT_TRUE(context.grimodex().secure_input());
  EXPECT_EQ(context.input_field_type(), commands::Context::PASSWORD);
  EXPECT_FALSE(context.has_preceding_text());
  EXPECT_FALSE(context.has_following_text());
}

}  // namespace
}  // namespace mozc::win32::tsf
