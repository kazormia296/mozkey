// Copyright 2026 The Mozkey Authors
// Licensed under the Apache License, Version 2.0.

#include "unix/fcitx5/raw_reading_recovery.h"

#include <cstddef>
#include <cstdint>
#include <string>

#include "protocol/commands.pb.h"
#include "unix/fcitx5/grimodex_context.h"
#include "unix/fcitx5/mozc_client_interface.h"

namespace fcitx {

RawReadingRecovery::PrepareResult RawReadingRecovery::Prepare(
    MozcClientInterface* client, const mozc::commands::Context& context,
    bool secure_input, mozc::commands::Output* recovered_output) {
  if (client == nullptr || recovered_output == nullptr ||
      recovery_in_progress_) {
    return PrepareResult::kFailed;
  }
  recovered_output->Clear();

  if (!client->EnsureSession()) {
    return PrepareResult::kFailed;
  }
  const uint64_t generation = client->session_generation();
  if (generation == 0) {
    return PrepareResult::kFailed;
  }

  if (observed_session_generation_ == 0) {
    observed_session_generation_ = generation;
    if (secure_input) {
      ClearReading();
    }
    return PrepareResult::kReady;
  }
  if (observed_session_generation_ == generation) {
    if (secure_input) {
      ClearReading();
    }
    return PrepareResult::kReady;
  }

  observed_session_generation_ = generation;
  last_snapshot_relation_ = SnapshotRelation::kUnknown;
  if (secure_input) {
    ClearReading();
    return PrepareResult::kSessionChanged;
  }
  if (journal_.empty() || journal_suppressed_) {
    return PrepareResult::kSessionChanged;
  }

  const std::string previous_digest = pinned_snapshot_digest_;
  recovery_in_progress_ = true;
  mozc::commands::Output latest_output;
  for (const mozc::commands::KeyEvent& key : journal_) {
    latest_output.Clear();
    if (!SendKeyWithGrimodexContext(client, key, context, &latest_output) ||
        client->session_generation() != generation ||
        HasHostVisibleSideEffect(latest_output) ||
        !latest_output.has_consumed() || !latest_output.consumed() ||
        !HasActiveComposition(latest_output)) {
      recovery_in_progress_ = false;
      if (client->session_generation() != 0) {
        observed_session_generation_ = client->session_generation();
      }
      SuppressReading();
      return PrepareResult::kFailed;
    }
  }
  recovery_in_progress_ = false;

  const std::string recovered_digest = SnapshotDigest(latest_output);
  if (!previous_digest.empty() && !recovered_digest.empty()) {
    last_snapshot_relation_ =
        previous_digest == recovered_digest ? SnapshotRelation::kSame
                                            : SnapshotRelation::kChanged;
  }
  if (!recovered_digest.empty()) {
    pinned_snapshot_digest_ = recovered_digest;
  }
  *recovered_output = latest_output;
  return PrepareResult::kSessionChanged;
}

bool RawReadingRecovery::DispatchKey(
    MozcClientInterface* client, const mozc::commands::KeyEvent& key,
    const mozc::commands::Context& context, bool secure_input,
    mozc::commands::Output* output) {
  if (client == nullptr || output == nullptr) {
    return false;
  }
  mozc::commands::Output recovered_output;
  if (Prepare(client, context, secure_input, &recovered_output) ==
      PrepareResult::kFailed) {
    return false;
  }
  if (SendKeyWithGrimodexContext(client, key, context, output)) {
    return true;
  }
  if (secure_input) {
    return false;
  }

  // Client deliberately does not retry managed protobuf requests.  Only a
  // newly observed generation authorizes this one raw-key retry.
  recovered_output.Clear();
  if (Prepare(client, context, /*secure_input=*/false, &recovered_output) !=
      PrepareResult::kSessionChanged) {
    return false;
  }
  return SendKeyWithGrimodexContext(client, key, context, output);
}

void RawReadingRecovery::RecordSuccessfulKey(
    const mozc::commands::KeyEvent& key,
    const mozc::commands::Output& output, bool secure_input) {
  if (secure_input) {
    ClearReading();
    return;
  }
  if (!output.has_consumed() || !output.consumed()) {
    return;
  }
  if (HasHostVisibleSideEffect(output) || !HasActiveComposition(output)) {
    ClearReading();
    return;
  }
  if (!IsRecoverableRawKey(key)) {
    // Keeping only the suffix after a conversion/navigation key would rebuild
    // a different reading at a different cursor position.  Drop the whole
    // journal and remain suppressed until the next composition boundary.
    SuppressReading();
    return;
  }
  if (journal_suppressed_) {
    return;
  }

  const size_t key_bytes = key.ByteSizeLong();
  if (journal_.size() >= kMaxEventCount ||
      key_bytes > kMaxSerializedBytes - journal_bytes_) {
    SuppressReading();
    return;
  }

  journal_.push_back(key);
  journal_bytes_ += key_bytes;
  const std::string digest = SnapshotDigest(output);
  if (!digest.empty()) {
    pinned_snapshot_digest_ = digest;
  }
}

void RawReadingRecovery::ClearReading() {
  journal_.clear();
  journal_bytes_ = 0;
  journal_suppressed_ = false;
  pinned_snapshot_digest_.clear();
  last_snapshot_relation_ = SnapshotRelation::kUnknown;
}

void RawReadingRecovery::ResetSessionBoundary() {
  ClearReading();
  observed_session_generation_ = 0;
  recovery_in_progress_ = false;
}

bool RawReadingRecovery::HasActiveComposition(
    const mozc::commands::Output& output) {
  return output.has_preedit() && output.preedit().segment_size() > 0;
}

bool RawReadingRecovery::HasHostVisibleSideEffect(
    const mozc::commands::Output& output) {
  return output.has_result() || output.has_deletion_range() ||
         (output.has_url() && !output.url().empty()) ||
         (output.has_launch_tool_mode() &&
          output.launch_tool_mode() != mozc::commands::Output::NO_TOOL);
}

bool RawReadingRecovery::IsRecoverableRawKey(
    const mozc::commands::KeyEvent& key) {
  const auto is_printable_code_point = [](uint32_t code) {
    return (code >= 0x21 && code <= 0x7E) ||
           (code >= 0xA0 && code <= 0x10FFFF &&
            !(code >= 0xD800 && code <= 0xDFFF));
  };

  if (key.probable_key_event_size() != 0 || key.has_activated() ||
      key.has_mode() || (key.has_modifiers() && key.modifiers() != 0) ||
      (key.has_input_style() &&
       key.input_style() != mozc::commands::KeyEvent::FOLLOW_MODE)) {
    return false;
  }

  for (const auto modifier : key.modifier_keys()) {
    switch (modifier) {
      case mozc::commands::KeyEvent::SHIFT:
      case mozc::commands::KeyEvent::LEFT_SHIFT:
      case mozc::commands::KeyEvent::RIGHT_SHIFT:
      case mozc::commands::KeyEvent::CAPS:
        break;
      default:
        // CTRL/ALT and key-up/down modifier events are actions, not reading.
        return false;
    }
  }

  if (key.has_special_key() &&
      key.special_key() != mozc::commands::KeyEvent::NO_SPECIALKEY) {
    return (key.special_key() == mozc::commands::KeyEvent::BACKSPACE ||
            key.special_key() == mozc::commands::KeyEvent::DEL) &&
           !key.has_key_code() &&
           (!key.has_key_string() || key.key_string().empty()) &&
           key.modifier_keys_size() == 0;
  }

  if (key.has_key_string() && !key.key_string().empty()) {
    for (const unsigned char byte : key.key_string()) {
      if (byte < 0x20 || byte == 0x7F) {
        return false;
      }
    }
    return !key.has_key_code() || is_printable_code_point(key.key_code());
  }
  if (!key.has_key_code()) {
    return false;
  }

  // Fcitx maps space and control/navigation keys to SpecialKey.  Accept only
  // printable Unicode scalar values here so an unknown action cannot enter
  // the recovery journal through key_code.
  return is_printable_code_point(key.key_code());
}

std::string RawReadingRecovery::SnapshotDigest(
    const mozc::commands::Output& output) {
  if (!output.has_grimodex_session_status() ||
      !output.grimodex_session_status().has_pinned_payload_sha256()) {
    return {};
  }
  return output.grimodex_session_status().pinned_payload_sha256();
}

void RawReadingRecovery::SuppressReading() {
  journal_.clear();
  journal_bytes_ = 0;
  journal_suppressed_ = true;
  pinned_snapshot_digest_.clear();
  last_snapshot_relation_ = SnapshotRelation::kUnknown;
}

}  // namespace fcitx
