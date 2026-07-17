// Copyright 2026 The Mozkey Authors
// Licensed under the Apache License, Version 2.0.

#include "unix/fcitx5/grimodex_context.h"

#include <cstdint>
#include <limits>
#include <string>
#include <string_view>
#include <vector>

#include "protocol/commands.pb.h"
#include "protocol/config.pb.h"
#include "testing/gunit.h"
#include "unix/fcitx5/mozc_client_interface.h"

namespace fcitx {
namespace {

class RecordingClient final : public MozcClientInterface {
 public:
  bool EnsureConnection() override { return true; }

  bool SendKeyWithContext(const mozc::commands::KeyEvent&,
                          const mozc::commands::Context& context,
                          mozc::commands::Output*) override {
    key_contexts.push_back(context);
    return true;
  }

  bool SendCommandWithContext(
      const mozc::commands::SessionCommand& command,
      const mozc::commands::Context& context,
      mozc::commands::Output*) override {
    command_types.push_back(command.type());
    command_contexts.push_back(context);
    return true;
  }

  bool IsDirectModeCommand(const mozc::commands::KeyEvent&) const override {
    return false;
  }
  bool GetConfig(mozc::config::Config*) override { return true; }
  void set_client_capability(const mozc::commands::Capability&) override {}
  bool SyncData() override { return true; }
  bool LaunchTool(const std::string&, std::string_view) override { return true; }
  bool LaunchToolWithProtoBuf(const mozc::commands::Output&) override {
    return true;
  }

  std::vector<mozc::commands::Context> key_contexts;
  std::vector<mozc::commands::SessionCommand::CommandType> command_types;
  std::vector<mozc::commands::Context> command_contexts;
};

TEST(GrimodexContextTest, SecureInputNeverCallsSurroundingOrClipboardProvider) {
  int provider_calls = 0;
  const mozc::commands::Context context = BuildGrimodexContext(
      "org.example.Editor", "wayland", /*secure_input=*/true,
      /*focus_epoch=*/42, [&provider_calls]() {
        ++provider_calls;
        return std::optional<GrimodexSurroundingText>(
            GrimodexSurroundingText{"secret-before", "secret-after"});
      });

  EXPECT_EQ(provider_calls, 0);
  ASSERT_TRUE(context.has_grimodex());
  EXPECT_TRUE(context.grimodex().secure_input());
  EXPECT_EQ(context.grimodex().focus_epoch(), 42);
  EXPECT_EQ(context.input_field_type(), mozc::commands::Context::PASSWORD);
  EXPECT_FALSE(context.has_preceding_text());
  EXPECT_FALSE(context.has_following_text());
}

TEST(GrimodexContextTest, NonSecureInputReadsSurroundingOnce) {
  int provider_calls = 0;
  const mozc::commands::Context context = BuildGrimodexContext(
      "org.example.Editor", "wayland", /*secure_input=*/false,
      /*focus_epoch=*/7, [&provider_calls]() {
        ++provider_calls;
        return std::optional<GrimodexSurroundingText>(
            GrimodexSurroundingText{"before", "after"});
      });

  EXPECT_EQ(provider_calls, 1);
  EXPECT_FALSE(context.grimodex().secure_input());
  EXPECT_EQ(context.preceding_text(), "before");
  EXPECT_EQ(context.following_text(), "after");
}

TEST(GrimodexContextTest, MetadataAndRevisionAreBounded) {
  const std::string huge_program(kMaxGrimodexProgramBytes + 1000, 'p');
  const std::string huge_frontend(kMaxGrimodexFrontendBytes + 1000, 'f');
  const mozc::commands::Context context = BuildGrimodexContext(
      huge_program, huge_frontend, /*secure_input=*/false,
      /*focus_epoch=*/0x80000000ULL);

  EXPECT_EQ(context.grimodex().program().size(), kMaxGrimodexProgramBytes);
  EXPECT_EQ(context.grimodex().frontend().size(), kMaxGrimodexFrontendBytes);
  EXPECT_EQ(context.revision(), 1);
  EXPECT_NE(context.revision(), 0);
}

TEST(GrimodexContextTest, FocusAndDomainTransitionsAdvanceNonzeroEpoch) {
  uint64_t epoch = 1;
  epoch = AdvanceGrimodexFocusEpoch(epoch);  // FocusIn.
  EXPECT_EQ(epoch, 2);
  epoch = AdvanceGrimodexFocusEpoch(epoch);  // Capability/domain change.
  EXPECT_EQ(epoch, 3);
  EXPECT_EQ(AdvanceGrimodexFocusEpoch(std::numeric_limits<uint64_t>::max()), 1);
}

TEST(GrimodexContextTest, EveryCommandClassCarriesIdenticalTypedContext) {
  RecordingClient client;
  const mozc::commands::Context context = BuildGrimodexContext(
      "same-program", "same-frontend", /*secure_input=*/false,
      /*focus_epoch=*/19);
  mozc::commands::Output output;

  mozc::commands::KeyEvent key;
  key.set_key_code('a');
  ASSERT_TRUE(
      SendKeyWithGrimodexContext(&client, key, context, &output));

  // SELECT, PAGING, RESET/FOCUS status and CALLBACK paths all eventually use
  // the same dispatcher in MozcState::TrySendRawCommand.
  for (const auto type : {
           mozc::commands::SessionCommand::SELECT_CANDIDATE,
           mozc::commands::SessionCommand::CONVERT_NEXT_PAGE,
           mozc::commands::SessionCommand::REVERT,
           mozc::commands::SessionCommand::GET_STATUS,
           mozc::commands::SessionCommand::UNDO,
           mozc::commands::SessionCommand::APPLY_LIVE_CONVERSION,
           mozc::commands::SessionCommand::APPLY_ZENZ_LIVE_CORRECTION,
       }) {
    mozc::commands::SessionCommand command;
    command.set_type(type);
    ASSERT_TRUE(
        SendCommandWithGrimodexContext(&client, command, context, &output));
  }

  ASSERT_EQ(client.key_contexts.size(), 1);
  ASSERT_EQ(client.command_contexts.size(), 7);
  for (const mozc::commands::Context& actual : client.command_contexts) {
    EXPECT_EQ(actual.SerializeAsString(), context.SerializeAsString());
  }
}

TEST(GrimodexContextTest, ContextlessDispatchIsRejected) {
  RecordingClient client;
  mozc::commands::SessionCommand command;
  command.set_type(mozc::commands::SessionCommand::GET_STATUS);
  mozc::commands::Output output;

  EXPECT_FALSE(SendCommandWithGrimodexContext(
      &client, command, mozc::commands::Context::default_instance(), &output));
  EXPECT_TRUE(client.command_contexts.empty());
}

}  // namespace
}  // namespace fcitx
