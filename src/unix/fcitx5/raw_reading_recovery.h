// Copyright 2026 The Mozkey Authors
// Licensed under the Apache License, Version 2.0.

#ifndef MOZC_UNIX_FCITX5_RAW_READING_RECOVERY_H_
#define MOZC_UNIX_FCITX5_RAW_READING_RECOVERY_H_

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "protocol/commands.pb.h"
#include "unix/fcitx5/mozc_client_interface.h"

namespace fcitx {

// This journal deliberately stores only raw KeyEvent messages.  SessionCommand
// messages, candidate IDs, callbacks, and effects are scoped to one Mozc
// session and must never be replayed into a replacement session.
class RawReadingRecovery final {
 public:
  static constexpr size_t kMaxEventCount = 256;
  static constexpr size_t kMaxSerializedBytes = 64 * 1024;

  enum class PrepareResult {
    kReady,
    kSessionChanged,
    kFailed,
  };

  enum class SnapshotRelation {
    kUnknown,
    kSame,
    kChanged,
  };

  using SessionInitializer = std::function<bool(
      MozcClientInterface*, uint64_t, const mozc::commands::Context&)>;

  // `session_initializer` must initialize exactly the supplied generation.
  // Prepare invokes it before adopting a new generation or replaying keys.
  explicit RawReadingRecovery(SessionInitializer session_initializer);

  // Ensures that a session exists and, when its generation changed, rebuilds
  // the non-secure reading from raw keys.  A kSessionChanged result tells the
  // caller to discard any pending SessionCommand.  `recovered_output` is the
  // last pure-composition response produced by replay, if one was available.
  PrepareResult Prepare(MozcClientInterface* client,
                        const mozc::commands::Context& context,
                        bool secure_input,
                        mozc::commands::Output* recovered_output);

  // Dispatches one current raw key.  If the first call discovers a destroyed
  // session, the old reading is reconstructed and this key is retried at most
  // once.  Secure input is never retried.
  bool DispatchKey(MozcClientInterface* client,
                   const mozc::commands::KeyEvent& key,
                   const mozc::commands::Context& context,
                   bool secure_input,
                   mozc::commands::Output* output);

  // Records a successfully dispatched user key only while it leaves an active
  // composition.  Any commit or other host-visible side effect terminates the
  // journal.
  void RecordSuccessfulKey(const mozc::commands::KeyEvent& key,
                           const mozc::commands::Output& output,
                           bool secure_input);

  // A local composition boundary (commit/reset) preserves knowledge of the
  // current server session but drops all reading data.
  void ClearReading();

  // A focus/domain/client ownership boundary drops both reading data and the
  // observed generation.  The next session is adopted as a fresh baseline.
  void ResetSessionBoundary();

  size_t journal_size_for_test() const { return journal_.size(); }
  size_t journal_bytes_for_test() const { return journal_bytes_; }
  bool journal_suppressed_for_test() const { return journal_suppressed_; }
  SnapshotRelation last_snapshot_relation_for_test() const {
    return last_snapshot_relation_;
  }

 private:
  static bool HasActiveComposition(const mozc::commands::Output& output);
  static bool HasHostVisibleSideEffect(const mozc::commands::Output& output);
  static bool IsRecoverableRawKey(const mozc::commands::KeyEvent& key);
  static std::string SnapshotDigest(const mozc::commands::Output& output);
  void BestEffortAbortPartialRecovery(
      MozcClientInterface* client, const mozc::commands::Context& context);
  void SuppressReading();

  std::vector<mozc::commands::KeyEvent> journal_;
  size_t journal_bytes_ = 0;
  uint64_t observed_session_generation_ = 0;
  bool journal_suppressed_ = false;
  bool recovery_in_progress_ = false;
  bool journal_invalidation_pending_ = false;
  std::string pinned_snapshot_digest_;
  SnapshotRelation last_snapshot_relation_ = SnapshotRelation::kUnknown;
  SessionInitializer session_initializer_;
};

}  // namespace fcitx

#endif  // MOZC_UNIX_FCITX5_RAW_READING_RECOVERY_H_
