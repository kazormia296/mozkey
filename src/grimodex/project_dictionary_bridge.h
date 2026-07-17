// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_PROJECT_DICTIONARY_BRIDGE_H_
#define MOZC_GRIMODEX_PROJECT_DICTIONARY_BRIDGE_H_

#include <cstdint>
#include <memory>
#include <string>

#include "absl/status/status.h"
#include "dictionary/project_dictionary.h"
#include "grimodex/protocol_v1.h"

namespace mozc::grimodex {

struct ProjectDictionaryPosIdPair final {
  uint16_t lid = 0;
  uint16_t rid = 0;
};

// Mozc POS IDs are data-version dependent, so the owner of the matching
// DataManager must resolve and provide all three pairs.  This bridge never
// embeds numeric IDs from a particular Mozc data version.
struct ProjectDictionaryPosIds final {
  ProjectDictionaryPosIdPair person;
  ProjectDictionaryPosIdPair place;
  ProjectDictionaryPosIdPair noun;
};

enum class ProjectDictionaryBridgeDiagnostic {
  kReady,
  kNullPublication,
  kInactive,
  kInvalidPublication,
  kInvalidPosIds,
  kNativeValidationFailed,
};

// One immutable composition-pinning unit.  The dictionary, Zenz prompt
// conditions, and source provenance can therefore never be observed at
// different publisher generations.  The provenance is copied from the
// verified Protocol V1 DTO; `dictionary->fingerprint()` is its compact,
// rehashed identity.
struct BridgedProjectSnapshot final {
  uint64_t generation = 0;
  std::shared_ptr<const dictionary::ProjectDictionarySnapshot> dictionary;
  ProjectConditionsDto conditions;
  std::string project_id;
  std::string project_name;
  std::string state_updated_at;
  std::string project_generated_at;
  std::string state_sha256;
  std::string project_sha256;
};

// A complete replacement decision, not a delta.  Every non-ready result has a
// null snapshot and must clear any previously installed bridged snapshot.  In
// particular, an old DTO retained by ProtocolV1SnapshotPublisher during its
// one retryable grace reload is never silently resurrected by this bridge.
struct ProjectDictionaryBridgeResult final {
  ProjectDictionaryBridgeDiagnostic diagnostic =
      ProjectDictionaryBridgeDiagnostic::kNullPublication;
  std::shared_ptr<const BridgedProjectSnapshot> snapshot;
  absl::Status status;

  bool ready() const {
    return diagnostic == ProjectDictionaryBridgeDiagnostic::kReady &&
           snapshot != nullptr && snapshot->dictionary != nullptr &&
           snapshot->generation == snapshot->dictionary->generation() &&
           status.ok();
  }
  bool should_clear() const { return !ready(); }
};

// Converts one publisher result into the immutable native dictionary and
// composition context bundle.
// Priority maps to Mozc cost as follows (lower is stronger):
//   priority 1 -> 3800, priority 2 -> 3600, priority 3 -> 3400.
// These deliberately positive, fixed costs make the mapping deterministic
// across data versions while preserving Protocol V1 priority ordering.
ProjectDictionaryBridgeResult BuildProjectDictionarySnapshot(
    const std::shared_ptr<const PublishedProtocolV1Snapshot>& published,
    const ProjectDictionaryPosIds& pos_ids);

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_PROJECT_DICTIONARY_BRIDGE_H_
