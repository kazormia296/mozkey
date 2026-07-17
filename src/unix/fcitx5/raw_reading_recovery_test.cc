// Copyright 2026 The Mozkey Authors
// Licensed under the Apache License, Version 2.0.

#include "unix/fcitx5/raw_reading_recovery.h"

#include <cstddef>
#include <cstdint>
#include <deque>
#include <string>
#include <string_view>
#include <vector>

#include "protocol/commands.pb.h"
#include "protocol/config.pb.h"
#include "testing/gunit.h"
#include "unix/fcitx5/mozc_client_interface.h"

namespace fcitx {
namespace {

mozc::commands::KeyEvent Key(char code) {
  mozc::commands::KeyEvent key;
  key.set_key_code(code);
  return key;
}

mozc::commands::Output ActiveOutput(std::string_view digest = {}) {
  mozc::commands::Output output;
  output.set_consumed(true);
  output.mutable_preedit()->set_cursor(1);
  auto* segment = output.mutable_preedit()->add_segment();
  segment->set_annotation(mozc::commands::Preedit::Segment::UNDERLINE);
  segment->set_value("a");
  segment->set_value_length(1);
  if (!digest.empty()) {
    output.mutable_grimodex_session_status()->set_pinned_payload_sha256(
        digest);
  }
  return output;
}

mozc::commands::Context ValidContext(bool secure_input = false) {
  mozc::commands::Context context;
  context.set_revision(1);
  context.mutable_grimodex()->set_program("raw-recovery-test");
  context.mutable_grimodex()->set_frontend("test");
  context.mutable_grimodex()->set_focus_epoch(1);
  context.mutable_grimodex()->set_secure_input(secure_input);
  if (secure_input) {
    context.set_input_field_type(mozc::commands::Context::PASSWORD);
  }
  return context;
}

class RecoveryTestClient final : public MozcClientInterface {
 public:
  bool EnsureConnection() override { return ensure_succeeds; }
  bool EnsureSession() override { return ensure_succeeds; }
  uint64_t session_generation() const override { return generation; }

  bool SendKeyWithContext(const mozc::commands::KeyEvent& key,
                          const mozc::commands::Context&,
                          mozc::commands::Output* output) override {
    sent_keys.push_back(key);
    if (recreate_on_next_key) {
      recreate_on_next_key = false;
      ++generation;
      return false;
    }
    if (fail_next_key) {
      fail_next_key = false;
      return false;
    }
    if (fail_on_key_call != 0 && sent_keys.size() == fail_on_key_call) {
      return false;
    }
    if (!queued_outputs.empty()) {
      *output = queued_outputs.front();
      queued_outputs.pop_front();
    } else {
      *output = ActiveOutput(default_digest);
    }
    return true;
  }

  bool SendCommandWithContext(const mozc::commands::SessionCommand& command,
                              const mozc::commands::Context&,
                              mozc::commands::Output*) override {
    ++sent_commands;
    sent_command_types.push_back(command.type());
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

  bool ensure_succeeds = true;
  bool fail_next_key = false;
  size_t fail_on_key_call = 0;
  bool recreate_on_next_key = false;
  uint64_t generation = 1;
  int sent_commands = 0;
  std::vector<mozc::commands::SessionCommand::CommandType> sent_command_types;
  std::string default_digest;
  std::deque<mozc::commands::Output> queued_outputs;
  std::vector<mozc::commands::KeyEvent> sent_keys;
};

TEST(RawReadingRecoveryTest, RecreatesCompositionFromRawKeysAfterSessionKill) {
  RecoveryTestClient client;
  client.default_digest = std::string(32, 'a');
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();

  EXPECT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('a'), ActiveOutput(client.default_digest),
                               false);
  recovery.RecordSuccessfulKey(Key('b'), ActiveOutput(client.default_digest),
                               false);

  client.generation = 2;  // Simulates server/session recreation.
  EXPECT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kSessionChanged);
  ASSERT_EQ(client.sent_keys.size(), 2);
  EXPECT_EQ(client.sent_keys[0].key_code(), 'a');
  EXPECT_EQ(client.sent_keys[1].key_code(), 'b');
  EXPECT_EQ(recovery.journal_size_for_test(), 2);
  EXPECT_EQ(recovery.last_snapshot_relation_for_test(),
            RawReadingRecovery::SnapshotRelation::kSame);
}

TEST(RawReadingRecoveryTest,
     CurrentRawKeyIsRetriedExactlyOnceAfterGenerationAdvances) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output output;
  const mozc::commands::Context context = ValidContext();
  ASSERT_EQ(recovery.Prepare(&client, context, false, &output),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('a'), ActiveOutput(), false);

  client.recreate_on_next_key = true;
  EXPECT_TRUE(recovery.DispatchKey(&client, Key('b'), context, false, &output));
  ASSERT_EQ(client.sent_keys.size(), 3);
  EXPECT_EQ(client.sent_keys[0].key_code(), 'b');  // Failed discovery call.
  EXPECT_EQ(client.sent_keys[1].key_code(), 'a');  // Reading reconstruction.
  EXPECT_EQ(client.sent_keys[2].key_code(), 'b');  // The only retry.
  EXPECT_EQ(client.generation, 2);
}

TEST(RawReadingRecoveryTest, SecureCurrentKeyIsNotRetriedAfterRecreation) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output output;
  const mozc::commands::Context context = ValidContext(true);
  ASSERT_EQ(recovery.Prepare(&client, context, true, &output),
            RawReadingRecovery::PrepareResult::kReady);

  client.recreate_on_next_key = true;
  EXPECT_FALSE(recovery.DispatchKey(&client, Key('p'), context, true, &output));
  ASSERT_EQ(client.sent_keys.size(), 1);
  EXPECT_EQ(client.sent_keys[0].key_code(), 'p');
}

TEST(RawReadingRecoveryTest,
     SessionChangedSignalDropsStaleCandidateAndSendsOnlyRawKeys) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();

  ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('x'), ActiveOutput(), false);
  client.generation = 2;

  // The adapter sees kSessionChanged and must return without dispatching the
  // pending SELECT_CANDIDATE command.  The recovery object itself has no API
  // capable of sending SessionCommand messages.
  EXPECT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kSessionChanged);
  EXPECT_EQ(client.sent_commands, 0);
  ASSERT_EQ(client.sent_keys.size(), 1);
  EXPECT_EQ(client.sent_keys[0].key_code(), 'x');
}

TEST(RawReadingRecoveryTest, SecureInputNeverJournalsOrRecoversRawKeys) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context nonsecure_context = ValidContext();
  const mozc::commands::Context secure_context = ValidContext(true);

  ASSERT_EQ(recovery.Prepare(&client, nonsecure_context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('s'), ActiveOutput(), false);
  ASSERT_EQ(recovery.journal_size_for_test(), 1);

  client.generation = 2;
  EXPECT_EQ(recovery.Prepare(&client, secure_context, true, &recovered),
            RawReadingRecovery::PrepareResult::kSessionChanged);
  EXPECT_TRUE(client.sent_keys.empty());
  EXPECT_EQ(recovery.journal_size_for_test(), 0);

  recovery.RecordSuccessfulKey(Key('p'), ActiveOutput(), true);
  EXPECT_EQ(recovery.journal_size_for_test(), 0);
}

TEST(RawReadingRecoveryTest, EventCapFailsClosedInsteadOfKeepingTail) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();
  ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);

  for (size_t i = 0; i < RawReadingRecovery::kMaxEventCount; ++i) {
    recovery.RecordSuccessfulKey(Key('a'), ActiveOutput(), false);
  }
  EXPECT_EQ(recovery.journal_size_for_test(),
            RawReadingRecovery::kMaxEventCount);
  recovery.RecordSuccessfulKey(Key('b'), ActiveOutput(), false);
  EXPECT_EQ(recovery.journal_size_for_test(), 0);
  EXPECT_TRUE(recovery.journal_suppressed_for_test());

  client.generation = 2;
  EXPECT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kSessionChanged);
  EXPECT_TRUE(client.sent_keys.empty());
}

TEST(RawReadingRecoveryTest,
     ConversionAndCandidateNavigationKeysSuppressWholeJournal) {
  for (const auto special_key : {
           mozc::commands::KeyEvent::SPACE,
           mozc::commands::KeyEvent::TAB,
           mozc::commands::KeyEvent::LEFT,
           mozc::commands::KeyEvent::DOWN,
           mozc::commands::KeyEvent::HENKAN,
       }) {
    RecoveryTestClient client;
    RawReadingRecovery recovery;
    mozc::commands::Output recovered;
    const mozc::commands::Context context = ValidContext();
    ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
              RawReadingRecovery::PrepareResult::kReady);
    recovery.RecordSuccessfulKey(Key('a'), ActiveOutput(), false);

    mozc::commands::KeyEvent action;
    action.set_special_key(special_key);
    recovery.RecordSuccessfulKey(action, ActiveOutput(), false);
    EXPECT_EQ(recovery.journal_size_for_test(), 0);
    EXPECT_TRUE(recovery.journal_suppressed_for_test());

    // A later character cannot become a misleading suffix-only journal.
    recovery.RecordSuccessfulKey(Key('b'), ActiveOutput(), false);
    EXPECT_EQ(recovery.journal_size_for_test(), 0);
  }
}

TEST(RawReadingRecoveryTest, ModifiedShortcutSuppressesWholeJournal) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();
  ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('a'), ActiveOutput(), false);

  mozc::commands::KeyEvent shortcut = Key('x');
  shortcut.add_modifier_keys(mozc::commands::KeyEvent::CTRL);
  recovery.RecordSuccessfulKey(shortcut, ActiveOutput(), false);
  EXPECT_EQ(recovery.journal_size_for_test(), 0);
  EXPECT_TRUE(recovery.journal_suppressed_for_test());
}

TEST(RawReadingRecoveryTest, BackspaceMayRemainInRawReadingJournal) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();
  ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('a'), ActiveOutput(), false);

  mozc::commands::KeyEvent backspace;
  backspace.set_special_key(mozc::commands::KeyEvent::BACKSPACE);
  recovery.RecordSuccessfulKey(backspace, ActiveOutput(), false);
  EXPECT_EQ(recovery.journal_size_for_test(), 2);
  EXPECT_FALSE(recovery.journal_suppressed_for_test());
}

TEST(RawReadingRecoveryTest, ByteCapFailsClosed) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();
  ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);

  mozc::commands::KeyEvent huge;
  huge.set_key_string(
      std::string(RawReadingRecovery::kMaxSerializedBytes + 1, 'x'));
  recovery.RecordSuccessfulKey(huge, ActiveOutput(), false);
  EXPECT_EQ(recovery.journal_size_for_test(), 0);
  EXPECT_TRUE(recovery.journal_suppressed_for_test());
}

TEST(RawReadingRecoveryTest, FocusBoundaryClearsJournalAndAdoptsFreshClient) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();
  ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('a'), ActiveOutput(), false);

  recovery.ResetSessionBoundary();  // Focus-out/domain change.
  client.generation = 1;            // A new client can restart its counter.
  EXPECT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  EXPECT_TRUE(client.sent_keys.empty());
  EXPECT_EQ(recovery.journal_size_for_test(), 0);
}

TEST(RawReadingRecoveryTest, DomainBoundaryDropsReadingWithoutReplay) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();
  ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('d'), ActiveOutput(), false);

  recovery.ResetSessionBoundary();  // Capability/domain change.
  client.generation = 2;
  EXPECT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  EXPECT_TRUE(client.sent_keys.empty());
  EXPECT_EQ(recovery.journal_size_for_test(), 0);
}

TEST(RawReadingRecoveryTest, FailedRecoveryIsBoundedAndDropsJournal) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();
  ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('a'), ActiveOutput(), false);

  client.generation = 2;
  client.fail_next_key = true;
  EXPECT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kFailed);
  EXPECT_EQ(client.sent_keys.size(), 1);
  EXPECT_TRUE(recovery.journal_suppressed_for_test());

  EXPECT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  EXPECT_EQ(client.sent_keys.size(), 1);
}

TEST(RawReadingRecoveryTest, PartialReplayIsRevertedBeforeFailingClosed) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();
  ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('a'), ActiveOutput(), false);
  recovery.RecordSuccessfulKey(Key('b'), ActiveOutput(), false);

  client.generation = 2;
  client.fail_on_key_call = 2;
  EXPECT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kFailed);
  ASSERT_EQ(client.sent_keys.size(), 2);
  EXPECT_EQ(client.sent_commands, 1);
  ASSERT_EQ(client.sent_command_types.size(), 1);
  EXPECT_EQ(client.sent_command_types[0],
            mozc::commands::SessionCommand::REVERT);
  EXPECT_TRUE(recovery.journal_suppressed_for_test());
}

TEST(RawReadingRecoveryTest, ChangedSnapshotStillReplaysRawReading) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();
  const std::string old_digest(32, 'o');
  const std::string new_digest(32, 'n');
  ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('a'), ActiveOutput(old_digest), false);

  client.generation = 2;
  client.default_digest = new_digest;
  EXPECT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kSessionChanged);
  ASSERT_EQ(client.sent_keys.size(), 1);
  EXPECT_EQ(recovery.last_snapshot_relation_for_test(),
            RawReadingRecovery::SnapshotRelation::kChanged);
}

TEST(RawReadingRecoveryTest, CommitClearsJournal) {
  RecoveryTestClient client;
  RawReadingRecovery recovery;
  mozc::commands::Output recovered;
  const mozc::commands::Context context = ValidContext();
  ASSERT_EQ(recovery.Prepare(&client, context, false, &recovered),
            RawReadingRecovery::PrepareResult::kReady);
  recovery.RecordSuccessfulKey(Key('a'), ActiveOutput(), false);

  mozc::commands::Output committed;
  committed.set_consumed(true);
  committed.mutable_result()->set_value("committed");
  recovery.RecordSuccessfulKey(Key('\n'), committed, false);
  EXPECT_EQ(recovery.journal_size_for_test(), 0);
}

}  // namespace
}  // namespace fcitx
