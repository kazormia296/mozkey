// Copyright 2026 The Mozkey Authors

#include "grimodex/client_context.h"

#include <cstdint>
#include <limits>
#include <optional>
#include <string>

#include "protocol/commands.pb.h"
#include "testing/gunit.h"

namespace mozc::grimodex {
namespace {

TEST(ClientContextTest, SecureInputNeverInvokesSurroundingTextProvider) {
  int provider_calls = 0;
  const commands::Context context = BuildClientContext(
      "org.example.Editor", "test-frontend", /*secure_input=*/true,
      /*focus_epoch=*/42, [&provider_calls]() {
        ++provider_calls;
        return std::optional<SurroundingText>(
            SurroundingText{"secret-before", "secret-after"});
      });

  EXPECT_EQ(provider_calls, 0);
  ASSERT_TRUE(context.has_grimodex());
  EXPECT_TRUE(context.grimodex().secure_input());
  EXPECT_EQ(context.grimodex().focus_epoch(), 42);
  EXPECT_EQ(context.input_field_type(), commands::Context::PASSWORD);
  EXPECT_FALSE(context.has_preceding_text());
  EXPECT_FALSE(context.has_following_text());
  EXPECT_TRUE(IsValidClientContext(context));
}

TEST(ClientContextTest, NonSecureInputLoadsSurroundingTextOnce) {
  int provider_calls = 0;
  const commands::Context context = BuildClientContext(
      "org.example.Editor", "test-frontend", /*secure_input=*/false,
      /*focus_epoch=*/7, [&provider_calls]() {
        ++provider_calls;
        return std::optional<SurroundingText>(
            SurroundingText{"before", "after"});
      });

  EXPECT_EQ(provider_calls, 1);
  EXPECT_FALSE(context.grimodex().secure_input());
  EXPECT_EQ(context.preceding_text(), "before");
  EXPECT_EQ(context.following_text(), "after");
  EXPECT_TRUE(IsValidClientContext(context));
}

TEST(ClientContextTest, BoundsMetadataAndMapsRevisionIntoSignedRange) {
  const std::string huge_program(kMaxClientProgramBytes + 1000, 'p');
  const std::string huge_frontend(kMaxClientFrontendBytes + 1000, 'f');
  const commands::Context context = BuildClientContext(
      huge_program, huge_frontend, /*secure_input=*/false,
      /*focus_epoch=*/0x80000000ULL);

  EXPECT_EQ(context.grimodex().program().size(), kMaxClientProgramBytes);
  EXPECT_EQ(context.grimodex().frontend().size(), kMaxClientFrontendBytes);
  EXPECT_EQ(context.revision(), 1);
  EXPECT_GT(context.revision(), 0);
  EXPECT_TRUE(IsValidClientContext(context));
}

TEST(ClientContextTest, BoundsJapaneseAndEmojiAtUtf8ScalarBoundaries) {
  const std::string hiragana_a = "\xE3\x81\x82";  // U+3042
  std::string japanese_program;
  for (int i = 0; i < 86; ++i) {
    japanese_program.append(hiragana_a);
  }
  const std::string grinning_face = "\xF0\x9F\x98\x80";  // U+1F600
  std::string emoji_frontend;
  for (int i = 0; i < 33; ++i) {
    emoji_frontend.append(grinning_face);
  }

  const commands::Context context = BuildClientContext(
      japanese_program, emoji_frontend, /*secure_input=*/false,
      /*focus_epoch=*/9);

  EXPECT_EQ(context.grimodex().program(), japanese_program.substr(0, 85 * 3));
  EXPECT_EQ(context.grimodex().program().size(), 255);
  EXPECT_EQ(context.grimodex().frontend(), emoji_frontend.substr(0, 32 * 4));
  EXPECT_EQ(context.grimodex().frontend().size(),
            kMaxClientFrontendBytes);
  EXPECT_TRUE(IsValidClientContext(context));
}

TEST(ClientContextTest, PreservesUtf8ScalarEndingExactlyAtByteLimit) {
  const std::string grinning_face = "\xF0\x9F\x98\x80";  // U+1F600
  const std::string exact_program =
      std::string(kMaxClientProgramBytes - grinning_face.size(), 'p') +
      grinning_face;

  const commands::Context context = BuildClientContext(
      exact_program, "frontend", /*secure_input=*/false, /*focus_epoch=*/10);

  EXPECT_EQ(context.grimodex().program(), exact_program);
  EXPECT_EQ(context.grimodex().program().size(), kMaxClientProgramBytes);
  EXPECT_TRUE(IsValidClientContext(context));
}

TEST(ClientContextTest, DropsCombiningScalarRatherThanSplittingAtLimit) {
  const std::string base =
      std::string(kMaxClientProgramBytes - 2, 'p') + "e";
  const std::string decomposed_accent = base + "\xCC\x81";  // U+0301

  const commands::Context context = BuildClientContext(
      decomposed_accent, "frontend", /*secure_input=*/false,
      /*focus_epoch=*/11);

  EXPECT_EQ(context.grimodex().program(), base);
  EXPECT_EQ(context.grimodex().program().size(),
            kMaxClientProgramBytes - 1);
  EXPECT_TRUE(IsValidClientContext(context));
}

TEST(ClientContextTest, FocusEpochAdvanceSkipsZeroAfterWrap) {
  EXPECT_EQ(AdvanceFocusEpoch(0), 1);
  EXPECT_EQ(AdvanceFocusEpoch(1), 2);
  EXPECT_EQ(AdvanceFocusEpoch(std::numeric_limits<uint64_t>::max()), 1);
}

TEST(ClientContextTest, ValidationRejectsMissingAndInconsistentContexts) {
  EXPECT_FALSE(
      IsValidClientContext(commands::Context::default_instance()));

  commands::Context missing_epoch = BuildClientContext(
      "program", "frontend", /*secure_input=*/false, /*focus_epoch=*/3);
  missing_epoch.mutable_grimodex()->clear_focus_epoch();
  EXPECT_FALSE(IsValidClientContext(missing_epoch));

  commands::Context inconsistent_revision = BuildClientContext(
      "program", "frontend", /*secure_input=*/false, /*focus_epoch=*/3);
  inconsistent_revision.set_revision(RevisionFromFocusEpoch(4));
  EXPECT_FALSE(IsValidClientContext(inconsistent_revision));

  commands::Context inconsistent_security = BuildClientContext(
      "program", "frontend", /*secure_input=*/false, /*focus_epoch=*/3);
  inconsistent_security.set_input_field_type(commands::Context::PASSWORD);
  EXPECT_FALSE(IsValidClientContext(inconsistent_security));
}

TEST(ClientContextTest, ValidationRejectsSecureContextWithText) {
  commands::Context context = BuildClientContext(
      "program", "frontend", /*secure_input=*/true, /*focus_epoch=*/11);
  context.set_preceding_text("secret");

  EXPECT_FALSE(IsValidClientContext(context));
}

TEST(ClientContextTest, ValidationRejectsMalformedUtf8Metadata) {
  commands::Context invalid_program = BuildClientContext(
      std::string("\xF0\x9F", 2), "frontend", /*secure_input=*/false,
      /*focus_epoch=*/12);
  EXPECT_FALSE(IsValidClientContext(invalid_program));

  commands::Context invalid_frontend = BuildClientContext(
      "program", std::string("\xED\xA0\x80", 3), /*secure_input=*/false,
      /*focus_epoch=*/13);
  EXPECT_FALSE(IsValidClientContext(invalid_frontend));
}

}  // namespace
}  // namespace mozc::grimodex
