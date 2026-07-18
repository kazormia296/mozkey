// Copyright 2026 The Mozkey Authors

#include "win32/tip/tip_grimodex_client_context.h"

#include <optional>
#include <string>

#include "grimodex/client_context.h"
#include "protocol/commands.pb.h"
#include "testing/gunit.h"

namespace mozc::win32::tsf {
namespace {

TEST(TipGrimodexClientContextTest, NormalizesExecutableBasenameExactly) {
  EXPECT_EQ(NormalizeExecutableBasename(""), "");
  EXPECT_EQ(NormalizeExecutableBasename("Grimodex.exe"), "grimodex");
  EXPECT_EQ(NormalizeExecutableBasename(
                R"(C:\Program Files\Grimodex\Grimodex.EXE)"),
            "grimodex");
  EXPECT_EQ(NormalizeExecutableBasename(R"(C:\bin\Tool.exe.exe)"),
            "tool.exe");
  EXPECT_EQ(NormalizeExecutableBasename("Tool.exe.backup"),
            "tool.exe.backup");
  EXPECT_EQ(NormalizeExecutableBasename(R"(C:\bin\)"), "");
}

TEST(TipGrimodexClientContextTest, EpochTracksDomainTransitions) {
  TipGrimodexDomainTracker tracker;
  EXPECT_NE(tracker.focus_epoch(), 0);

  const uint64_t initial = tracker.Observe("grimodex", false);
  EXPECT_EQ(tracker.Observe("grimodex", false), initial);

  const uint64_t application_changed = tracker.Observe("notepad", false);
  EXPECT_NE(application_changed, initial);
  EXPECT_EQ(tracker.Observe("notepad", false), application_changed);

  const uint64_t secure_changed = tracker.Observe("notepad", true);
  EXPECT_NE(secure_changed, application_changed);

  tracker.OnFocusChanged();
  EXPECT_NE(tracker.focus_epoch(), secure_changed);
  EXPECT_NE(tracker.focus_epoch(), 0);
}

TEST(TipGrimodexClientContextTest, SecureContextNeverReadsSurroundingText) {
  bool surrounding_text_requested = false;
  const commands::Context context = BuildTsfClientContext(
      "grimodex", true, 7,
      [&]() -> std::optional<grimodex::SurroundingText> {
        surrounding_text_requested = true;
        return grimodex::SurroundingText{"secret-before", "secret-after"};
      });

  EXPECT_FALSE(surrounding_text_requested);
  ASSERT_TRUE(context.has_grimodex());
  EXPECT_EQ(context.grimodex().program(), "grimodex");
  EXPECT_EQ(context.grimodex().frontend(), "tsf");
  EXPECT_TRUE(context.grimodex().secure_input());
  EXPECT_EQ(context.grimodex().focus_epoch(), 7);
  EXPECT_EQ(context.input_field_type(), commands::Context::PASSWORD);
  EXPECT_FALSE(context.has_preceding_text());
  EXPECT_FALSE(context.has_following_text());
  EXPECT_TRUE(grimodex::IsValidClientContext(context));
}

TEST(TipGrimodexClientContextTest, NonSecureContextLoadsSurroundingText) {
  bool surrounding_text_requested = false;
  const commands::Context context = BuildTsfClientContext(
      "notepad", false, 9,
      [&]() -> std::optional<grimodex::SurroundingText> {
        surrounding_text_requested = true;
        return grimodex::SurroundingText{"before", "after"};
      });

  EXPECT_TRUE(surrounding_text_requested);
  EXPECT_EQ(context.preceding_text(), "before");
  EXPECT_EQ(context.following_text(), "after");
  EXPECT_TRUE(grimodex::IsValidClientContext(context));
}

}  // namespace
}  // namespace mozc::win32::tsf
