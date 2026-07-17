// Copyright 2026 Grimodex Contributors
//
// Licensed under the same terms as Mozc.

#ifndef MOZC_DICTIONARY_PROJECT_DICTIONARY_H_
#define MOZC_DICTIONARY_PROJECT_DICTIONARY_H_

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "absl/status/statusor.h"
#include "absl/strings/string_view.h"
#include "absl/synchronization/mutex.h"
#include "dictionary/dictionary_interface.h"

namespace mozc::dictionary {

// A fully resolved entry for a project-scoped dictionary.  POS IDs and costs
// are intentionally Mozc-native values; the platform ingress layer is
// responsible for mapping the portable project category before publishing a
// snapshot.
struct ProjectDictionaryEntry {
  std::string key;
  std::string value;
  int cost = 5000;
  uint16_t lid = 0;
  uint16_t rid = 0;
  uint8_t priority = 1;
  std::string entry_id;
};

// Immutable, in-memory dictionary data for one published project generation.
// It deliberately implements only lookup operations and has no reload, sync,
// import, or persistence path.
class ProjectDictionarySnapshot final : public DictionaryInterface {
 public:
  static absl::StatusOr<std::shared_ptr<const ProjectDictionarySnapshot>>
  Create(uint64_t generation, absl::string_view source_id,
         absl::string_view fingerprint,
         std::vector<ProjectDictionaryEntry> entries);

  uint64_t generation() const { return generation_; }
  absl::string_view source_id() const { return source_id_; }
  absl::string_view fingerprint() const { return fingerprint_; }
  size_t size() const { return entries_.size(); }

  bool HasKey(absl::string_view key) const override;
  bool HasValue(absl::string_view value) const override;
  void LookupPredictive(absl::string_view key,
                        Callback* callback) const override;
  void LookupPrefix(absl::string_view key, Callback* callback) const override;
  void LookupExact(absl::string_view key, Callback* callback) const override;

 private:
  struct StoredEntry {
    Token token;
    uint8_t priority = 1;
    std::string entry_id;
  };

  enum class EmitResult {
    kContinue,
    kDone,
    kCull,
  };

  ProjectDictionarySnapshot(uint64_t generation, std::string source_id,
                            std::string fingerprint,
                            std::vector<StoredEntry> entries);

  using EntryIterator = std::vector<StoredEntry>::const_iterator;
  EmitResult EmitKey(EntryIterator begin, EntryIterator end,
                     Callback* callback) const;
  std::pair<EntryIterator, EntryIterator> EqualRange(
      absl::string_view key) const;

  const uint64_t generation_;
  const std::string source_id_;
  const std::string fingerprint_;
  const std::vector<StoredEntry> entries_;
};

// Session-facing publication state.  One registry is owned by one
// EngineConverter/session; it must never be shared across Mozc sessions.
// Publishing replaces only `latest`; a composition keeps using its pinned
// immutable generation until the next pin.
class ProjectDictionaryRegistry final {
 public:
  enum class PublishResult {
    kApplied,
    kUnchanged,
    kRejectedNull,
    kRejectedSecure,
    kRejectedStale,
    kRejectedConflict,
  };

  struct Status {
    bool secure_input = false;
    std::optional<uint64_t> latest_generation;
    std::optional<uint64_t> pinned_generation;
  };

  ProjectDictionaryRegistry() = default;
  // Copying creates an independent session registry while safely sharing its
  // immutable snapshots.  This is used only when EngineConverter is cloned;
  // subsequent publish, pin, and secure purge operations remain isolated.
  ProjectDictionaryRegistry(const ProjectDictionaryRegistry& other);
  ProjectDictionaryRegistry& operator=(
      const ProjectDictionaryRegistry& other);

  PublishResult Publish(
      std::shared_ptr<const ProjectDictionarySnapshot> snapshot);

  // Pins the latest generation for a newly started composition.  Returns null
  // while secure input is active or when nothing has been published.
  std::shared_ptr<const ProjectDictionarySnapshot> PinForComposition();
  void EndComposition();

  // Entering secure input synchronously releases both latest and pinned
  // references.  Leaving secure input does not resurrect the old generation;
  // the client must publish a fresh snapshot.
  void SetSecureInput(bool secure);
  void Purge();

  bool secure_input() const;
  Status status() const;
  std::shared_ptr<const ProjectDictionarySnapshot> latest() const;
  std::shared_ptr<const ProjectDictionarySnapshot> pinned() const;

 private:
  mutable absl::Mutex mutex_;
  bool secure_input_ = false;
  std::shared_ptr<const ProjectDictionarySnapshot> latest_;
  std::shared_ptr<const ProjectDictionarySnapshot> pinned_;
};

}  // namespace mozc::dictionary

#endif  // MOZC_DICTIONARY_PROJECT_DICTIONARY_H_
