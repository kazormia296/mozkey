// Copyright 2026 The Mozkey Authors

#include "grimodex/project_dictionary_bridge.h"

#include <cstdint>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "absl/status/status.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/string_view.h"
#include "dictionary/project_dictionary.h"
#include "grimodex/protocol_v1.h"

namespace mozc::grimodex {
namespace {

constexpr int kPriorityCostBase = 4000;
constexpr int kPriorityCostStep = 200;

ProjectDictionaryBridgeResult Failure(
    ProjectDictionaryBridgeDiagnostic diagnostic, absl::Status status) {
  return ProjectDictionaryBridgeResult{
      .diagnostic = diagnostic,
      .snapshot = nullptr,
      .status = std::move(status),
  };
}

bool IsValidPosIdPair(const ProjectDictionaryPosIdPair& pair) {
  // Zero is reserved for the non-word/BOS domain, and 0xffff is the sentinel
  // used by PosMatcher tables.  A dictionary word must use neither.
  return pair.lid != 0 && pair.rid != 0 &&
         pair.lid != std::numeric_limits<uint16_t>::max() &&
         pair.rid != std::numeric_limits<uint16_t>::max();
}

bool IsValidPosIds(const ProjectDictionaryPosIds& pos_ids) {
  return IsValidPosIdPair(pos_ids.person) &&
         IsValidPosIdPair(pos_ids.place) &&
         IsValidPosIdPair(pos_ids.noun);
}

bool IsLowerHexSha256(absl::string_view digest) {
  if (digest.size() != 64) {
    return false;
  }
  for (const char byte : digest) {
    if (!((byte >= '0' && byte <= '9') ||
          (byte >= 'a' && byte <= 'f'))) {
      return false;
    }
  }
  return true;
}

std::string Fingerprint(const ProtocolV1Snapshot& snapshot) {
  // Hash the two independently verified source digests into one fixed-width
  // identity.  Labels and separators make the preimage format unambiguous.
  const std::string preimage =
      absl::StrCat("grimodex-protocol-v1\nstate=", snapshot.state_sha256,
                   "\nproject=", snapshot.project_sha256, "\n");
  return absl::StrCat("sha256:",
                      VerifiedFileBytes::FromBytes(preimage).sha256);
}

const ProjectDictionaryPosIdPair* PosIdsForCategory(
    DictionaryCategory category, const ProjectDictionaryPosIds& pos_ids) {
  switch (category) {
    case DictionaryCategory::kPerson:
      return &pos_ids.person;
    case DictionaryCategory::kPlace:
      return &pos_ids.place;
    case DictionaryCategory::kNoun:
      return &pos_ids.noun;
  }
  return nullptr;
}

int CostForPriority(int priority) {
  if (priority < 1 || priority > 3) {
    return 0;
  }
  return kPriorityCostBase - kPriorityCostStep * priority;
}

}  // namespace

ProjectDictionaryBridgeResult BuildProjectDictionarySnapshot(
    const std::shared_ptr<const PublishedProtocolV1Snapshot>& published,
    const ProjectDictionaryPosIds& pos_ids) {
  if (published == nullptr) {
    return Failure(ProjectDictionaryBridgeDiagnostic::kNullPublication,
                   absl::InvalidArgumentError(
                       "Grimodex publication pointer is null"));
  }
  if (published->diagnostic == LoadDiagnostic::kInactive &&
      published->snapshot == nullptr) {
    return Failure(ProjectDictionaryBridgeDiagnostic::kInactive,
                   absl::OkStatus());
  }

  // A fresh load and the publisher's deliberate one-reload retryable grace
  // are usable.  On a second consecutive retryable failure the publisher
  // supplies null, which takes the fail-closed path below.
  const bool retryable_grace =
      published->diagnostic == LoadDiagnostic::kMissingSnapshot ||
      published->diagnostic == LoadDiagnostic::kStateChangedDuringRead;
  if ((published->diagnostic != LoadDiagnostic::kLoaded &&
       !retryable_grace) ||
      published->snapshot == nullptr) {
    return Failure(ProjectDictionaryBridgeDiagnostic::kInvalidPublication,
                   absl::FailedPreconditionError(
                       "Grimodex publication has no usable snapshot"));
  }
  if (!IsValidPosIds(pos_ids)) {
    return Failure(ProjectDictionaryBridgeDiagnostic::kInvalidPosIds,
                   absl::InvalidArgumentError(
                       "Grimodex dictionary POS IDs are invalid"));
  }

  const ProtocolV1Snapshot& source = *published->snapshot;
  if (!IsLowerHexSha256(source.state_sha256) ||
      !IsLowerHexSha256(source.project_sha256)) {
    return Failure(ProjectDictionaryBridgeDiagnostic::kInvalidPublication,
                   absl::InvalidArgumentError(
                       "Grimodex source digest is not a lowercase SHA-256"));
  }

  std::vector<dictionary::ProjectDictionaryEntry> entries;
  entries.reserve(source.entries.size());
  for (const DictionaryEntryDto& entry : source.entries) {
    const ProjectDictionaryPosIdPair* const pair =
        PosIdsForCategory(entry.category, pos_ids);
    const int cost = CostForPriority(entry.priority);
    if (pair == nullptr || cost <= 0) {
      return Failure(ProjectDictionaryBridgeDiagnostic::kInvalidPublication,
                     absl::InvalidArgumentError(
                         "Grimodex dictionary category or priority is "
                         "invalid"));
    }
    entries.push_back(dictionary::ProjectDictionaryEntry{
        .key = entry.yomi,
        .value = entry.surface,
        .cost = cost,
        .lid = pair->lid,
        .rid = pair->rid,
        .priority = static_cast<uint8_t>(entry.priority),
        .entry_id = entry.entry_id,
    });
  }

  // Native Create is intentionally the final trust boundary.  It rechecks
  // UTF-8, scalar, identifier, count, payload, priority, and positive-cost
  // invariants even when the DTO did not originate from ProtocolV1Loader.
  auto native = dictionary::ProjectDictionarySnapshot::Create(
      published->sequence, source.project_id, Fingerprint(source),
      std::move(entries),
      dictionary::ProjectDictionaryMetadata{
          .topic = source.conditions.topic,
          .style = source.conditions.style,
          .preference = source.conditions.preference,
          .payload_sha256 = source.project_sha256,
      });
  if (!native.ok()) {
    return Failure(
        ProjectDictionaryBridgeDiagnostic::kNativeValidationFailed,
        native.status());
  }
  auto bridged = std::make_shared<const BridgedProjectSnapshot>(
      BridgedProjectSnapshot{
          .generation = published->sequence,
          .dictionary = *std::move(native),
          .conditions = source.conditions,
          .project_id = source.project_id,
          .project_name = source.project_name,
          .state_updated_at = source.state_updated_at,
          .project_generated_at = source.project_generated_at,
          .state_sha256 = source.state_sha256,
          .project_sha256 = source.project_sha256,
      });
  return ProjectDictionaryBridgeResult{
      .diagnostic = ProjectDictionaryBridgeDiagnostic::kReady,
      .snapshot = std::move(bridged),
      .status = absl::OkStatus(),
  };
}

}  // namespace mozc::grimodex
