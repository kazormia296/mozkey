// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_PROTOCOL_V1_H_
#define MOZC_GRIMODEX_PROTOCOL_V1_H_

#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "absl/status/statusor.h"
#include "absl/strings/string_view.h"

namespace mozc::grimodex {

struct ProtocolV1Limits final {
  static constexpr size_t kStateBytes = 65'536;
  static constexpr size_t kProjectBytes = 16'777'216;
  static constexpr size_t kProjectEntries = 20'000;
  static constexpr size_t kProjectIdScalars = 128;
  static constexpr size_t kProjectNameScalars = 256;
  static constexpr size_t kEntryYomiScalars = 256;
  static constexpr size_t kEntrySurfaceScalars = 256;
  static constexpr size_t kEntryIdScalars = 128;
  static constexpr size_t kProfileScalars = 400;
  static constexpr size_t kZenzaiConditionScalars = 200;
  static constexpr size_t kConverterConditionScalars = 25;
  static constexpr size_t kTimestampBytes = 64;
};

enum class DictionaryCategory {
  kPerson,
  kPlace,
  kNoun,
};

struct DictionaryEntryDto {
  std::string yomi;
  std::string surface;
  DictionaryCategory category = DictionaryCategory::kNoun;
  int priority = 0;
  std::string entry_id;

  bool operator==(const DictionaryEntryDto &) const = default;
};

struct ProjectConditionsDto {
  std::optional<std::string> topic;
  std::optional<std::string> style;
  std::optional<std::string> preference;

  bool operator==(const ProjectConditionsDto &) const = default;
};

// An immutable instance is shared with sessions.  Source timestamps and
// digests are provenance; SemanticallyEquals intentionally ignores them so a
// timestamp-only rewrite does not advance the publication sequence.
struct ProtocolV1Snapshot {
  std::string project_id;
  std::string project_name;
  std::vector<DictionaryEntryDto> entries;
  ProjectConditionsDto conditions;
  std::string state_updated_at;
  std::string project_generated_at;
  std::string state_sha256;
  std::string project_sha256;

  bool SemanticallyEquals(const ProtocolV1Snapshot &other) const;
};

enum class LoadDiagnostic {
  kLoaded,
  kInactive,
  kMissingState,
  kMissingSnapshot,
  kInvalidState,
  kInvalidSnapshot,
  kStateChangedDuringRead,
};

struct LoadResult {
  std::shared_ptr<const ProtocolV1Snapshot> snapshot;
  LoadDiagnostic diagnostic = LoadDiagnostic::kInactive;

  bool IsRetryable() const;
};

struct VerifiedFileBytes {
  std::string bytes;
  std::string sha256;

  static VerifiedFileBytes FromBytes(std::string bytes);
};

// Injectable at the exact state/project/state read boundary.  Platform
// integrations provide the filesystem implementation; tests can
// deterministically exercise stale publication races without weakening the
// production filesystem checks.
class ProtocolV1FileReader {
 public:
  virtual ~ProtocolV1FileReader() = default;
  virtual absl::StatusOr<VerifiedFileBytes> ReadState(size_t max_bytes) = 0;
  virtual absl::StatusOr<VerifiedFileBytes> ReadProject(
      absl::string_view project_id, size_t max_bytes) = 0;
};

class ProtocolV1Loader {
 public:
  struct Options {
    // Number of immediate retries after a retryable S1/project/S2 race.
    size_t stale_retries = 1;
  };

  explicit ProtocolV1Loader(std::shared_ptr<ProtocolV1FileReader> reader);
  ProtocolV1Loader(std::shared_ptr<ProtocolV1FileReader> reader,
                   Options options);

  LoadResult Load();
  LoadResult LoadOnce();

 private:
  std::shared_ptr<ProtocolV1FileReader> reader_;
  Options options_;
};

struct PublishedProtocolV1Snapshot {
  uint64_t sequence = 0;
  std::shared_ptr<const ProtocolV1Snapshot> snapshot;
  LoadDiagnostic diagnostic = LoadDiagnostic::kInactive;
};

// Publishes one immutable object at a time.  Readers never observe a partially
// replaced DTO, and the reload mutex serializes the state/project/state read
// sequence across concurrent watcher callbacks.
class ProtocolV1SnapshotPublisher {
 public:
  explicit ProtocolV1SnapshotPublisher(
      std::shared_ptr<ProtocolV1Loader> loader);

  std::shared_ptr<const PublishedProtocolV1Snapshot> Reload();
  std::shared_ptr<const PublishedProtocolV1Snapshot> Latest() const;

 private:
  std::shared_ptr<ProtocolV1Loader> loader_;
  mutable std::mutex reload_mutex_;
  mutable std::mutex published_mutex_;
  size_t consecutive_retryable_failures_ = 0;
  std::shared_ptr<const PublishedProtocolV1Snapshot> published_;
};

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_PROTOCOL_V1_H_
