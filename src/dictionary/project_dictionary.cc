// Copyright 2026 Grimodex Contributors
//
// Licensed under the same terms as Mozc.

#include "dictionary/project_dictionary.h"

#include <algorithm>
#include <memory>
#include <optional>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include "absl/status/status.h"
#include "absl/strings/match.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/string_view.h"
#include "base/strings/unicode.h"
#include "dictionary/dictionary_token.h"

namespace mozc::dictionary {
namespace {

using ResultType = DictionaryInterface::Callback::ResultType;

constexpr size_t kMaxEntryCount = 20'000;
constexpr size_t kMaxPayloadBytes = 16 * 1024 * 1024;
constexpr size_t kMaxTextScalars = 256;
constexpr size_t kMaxIdentifierBytes = 128;
constexpr size_t kMaxConditionScalars = 400;

absl::Status ValidateIdentifier(absl::string_view value,
                                absl::string_view field_name) {
  if (value.empty()) {
    return absl::InvalidArgumentError(
        absl::StrCat(field_name, " must not be empty"));
  }
  if (value.size() > kMaxIdentifierBytes || !strings::IsValidUtf8(value)) {
    return absl::InvalidArgumentError(
        absl::StrCat(field_name, " is invalid or too large"));
  }
  return absl::OkStatus();
}

absl::Status ValidateCondition(const std::optional<std::string>& value,
                               absl::string_view field_name) {
  if (!value.has_value()) {
    return absl::OkStatus();
  }
  if (!strings::IsValidUtf8(*value) ||
      strings::AtLeastCharsLen(*value, kMaxConditionScalars + 1) >
          kMaxConditionScalars) {
    return absl::InvalidArgumentError(
        absl::StrCat(field_name, " is invalid or too large"));
  }
  return absl::OkStatus();
}

bool IsLowerHexSha256(absl::string_view digest) {
  if (digest.empty()) {
    return true;
  }
  if (digest.size() != 64) {
    return false;
  }
  return std::all_of(digest.begin(), digest.end(), [](char c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
  });
}

}  // namespace

absl::StatusOr<std::shared_ptr<const ProjectDictionarySnapshot>>
ProjectDictionarySnapshot::Create(
    uint64_t generation, absl::string_view source_id,
    absl::string_view fingerprint,
    std::vector<ProjectDictionaryEntry> entries,
    ProjectDictionaryMetadata metadata) {
  if (entries.size() > kMaxEntryCount) {
    return absl::ResourceExhaustedError("too many project dictionary entries");
  }
  if (const absl::Status status = ValidateIdentifier(source_id, "source_id");
      !status.ok()) {
    return status;
  }
  if (const absl::Status status =
          ValidateIdentifier(fingerprint, "fingerprint");
      !status.ok()) {
    return status;
  }
  if (const absl::Status status = ValidateCondition(metadata.topic, "topic");
      !status.ok()) {
    return status;
  }
  if (const absl::Status status = ValidateCondition(metadata.style, "style");
      !status.ok()) {
    return status;
  }
  if (const absl::Status status =
          ValidateCondition(metadata.preference, "preference");
      !status.ok()) {
    return status;
  }
  if (!IsLowerHexSha256(metadata.payload_sha256)) {
    return absl::InvalidArgumentError(
        "payload_sha256 must be empty or lower-case hexadecimal SHA-256");
  }

  std::vector<StoredEntry> stored_entries;
  stored_entries.reserve(entries.size());
  size_t payload_bytes = source_id.size() + fingerprint.size() +
                         metadata.topic.value_or("").size() +
                         metadata.style.value_or("").size() +
                         metadata.preference.value_or("").size() +
                         metadata.payload_sha256.size();
  for (ProjectDictionaryEntry& entry : entries) {
    if (entry.key.empty() || entry.value.empty()) {
      return absl::InvalidArgumentError(
          "project dictionary keys and values must not be empty");
    }
    if (!strings::IsValidUtf8(entry.key) ||
        !strings::IsValidUtf8(entry.value) ||
        strings::AtLeastCharsLen(entry.key, kMaxTextScalars + 1) >
            kMaxTextScalars ||
        strings::AtLeastCharsLen(entry.value, kMaxTextScalars + 1) >
            kMaxTextScalars) {
      return absl::InvalidArgumentError(
          "project dictionary key or value is invalid or too large");
    }
    if (const absl::Status status =
            ValidateIdentifier(entry.entry_id, "entry_id");
        !status.ok()) {
      return status;
    }
    payload_bytes += entry.key.size() + entry.value.size() +
                     entry.entry_id.size() + sizeof(entry.cost) +
                     sizeof(entry.lid) + sizeof(entry.rid) +
                     sizeof(entry.priority);
    if (payload_bytes > kMaxPayloadBytes) {
      return absl::ResourceExhaustedError(
          "project dictionary payload is too large");
    }
    if (entry.cost <= 0) {
      return absl::InvalidArgumentError(
          absl::StrCat("project dictionary cost must be positive: ",
                       entry.entry_id));
    }
    if (entry.priority < 1 || entry.priority > 3) {
      return absl::InvalidArgumentError(
          absl::StrCat("project dictionary priority must be in [1, 3]: ",
                       entry.entry_id));
    }
    StoredEntry stored;
    stored.token = Token(std::move(entry.key), std::move(entry.value),
                         entry.cost, entry.lid, entry.rid,
                         Token::PROJECT_DICTIONARY);
    stored.priority = entry.priority;
    stored.entry_id = std::move(entry.entry_id);
    stored_entries.push_back(std::move(stored));
  }

  // First group equal Mozc token identities and retain their authoritative
  // project entry (higher priority, then stable UTF-8 entry ID).
  std::sort(stored_entries.begin(), stored_entries.end(),
            [](const StoredEntry& lhs, const StoredEntry& rhs) {
              const auto lhs_identity = std::tie(
                  lhs.token.key, lhs.token.value, lhs.token.lid, lhs.token.rid);
              const auto rhs_identity = std::tie(
                  rhs.token.key, rhs.token.value, rhs.token.lid, rhs.token.rid);
              if (lhs_identity != rhs_identity) {
                return lhs_identity < rhs_identity;
              }
              if (lhs.priority != rhs.priority) {
                return lhs.priority > rhs.priority;
              }
              return lhs.entry_id < rhs.entry_id;
            });
  stored_entries.erase(
      std::unique(stored_entries.begin(), stored_entries.end(),
                  [](const StoredEntry& lhs, const StoredEntry& rhs) {
                    return std::tie(lhs.token.key, lhs.token.value,
                                    lhs.token.lid, lhs.token.rid) ==
                           std::tie(rhs.token.key, rhs.token.value,
                                    rhs.token.lid, rhs.token.rid);
                  }),
      stored_entries.end());

  // Lookup order is deterministic and presents higher-priority entries first
  // for the same reading.
  std::sort(stored_entries.begin(), stored_entries.end(),
            [](const StoredEntry& lhs, const StoredEntry& rhs) {
              if (lhs.token.key != rhs.token.key) {
                return lhs.token.key < rhs.token.key;
              }
              if (lhs.priority != rhs.priority) {
                return lhs.priority > rhs.priority;
              }
              return std::tie(lhs.token.value, lhs.token.lid, lhs.token.rid,
                              lhs.entry_id) <
                     std::tie(rhs.token.value, rhs.token.lid, rhs.token.rid,
                              rhs.entry_id);
            });

  return std::shared_ptr<const ProjectDictionarySnapshot>(
      new ProjectDictionarySnapshot(generation, std::string(source_id),
                                    std::string(fingerprint),
                                    std::move(stored_entries),
                                    std::move(metadata)));
}

ProjectDictionarySnapshot::ProjectDictionarySnapshot(
    uint64_t generation, std::string source_id, std::string fingerprint,
    std::vector<StoredEntry> entries, ProjectDictionaryMetadata metadata)
    : generation_(generation),
      source_id_(std::move(source_id)),
      fingerprint_(std::move(fingerprint)),
      entries_(std::move(entries)),
      metadata_(std::move(metadata)) {}

ProjectDictionaryRegistry::ProjectDictionaryRegistry(
    const ProjectDictionaryRegistry& other) {
  absl::MutexLock lock(other.mutex_);
  secure_input_ = other.secure_input_;
  latest_ = other.latest_;
  pinned_ = other.pinned_;
}

ProjectDictionaryRegistry& ProjectDictionaryRegistry::operator=(
    const ProjectDictionaryRegistry& other) {
  if (this == &other) {
    return *this;
  }

  bool secure_input = false;
  std::shared_ptr<const ProjectDictionarySnapshot> latest;
  std::shared_ptr<const ProjectDictionarySnapshot> pinned;
  {
    absl::MutexLock lock(other.mutex_);
    secure_input = other.secure_input_;
    latest = other.latest_;
    pinned = other.pinned_;
  }
  {
    absl::MutexLock lock(mutex_);
    secure_input_ = secure_input;
    latest_ = std::move(latest);
    pinned_ = std::move(pinned);
  }
  return *this;
}

std::pair<ProjectDictionarySnapshot::EntryIterator,
          ProjectDictionarySnapshot::EntryIterator>
ProjectDictionarySnapshot::EqualRange(absl::string_view key) const {
  const auto begin = std::lower_bound(
      entries_.begin(), entries_.end(), key,
      [](const StoredEntry& entry, absl::string_view needle) {
        return entry.token.key < needle;
      });
  const auto end = std::upper_bound(
      begin, entries_.end(), key,
      [](absl::string_view needle, const StoredEntry& entry) {
        return needle < entry.token.key;
      });
  return {begin, end};
}

ProjectDictionarySnapshot::EmitResult ProjectDictionarySnapshot::EmitKey(
    EntryIterator begin, EntryIterator end, Callback* callback) const {
  if (begin == end) {
    return EmitResult::kContinue;
  }
  const absl::string_view key = begin->token.key;
  ResultType result = callback->OnKey(key);
  if (result == Callback::TRAVERSE_DONE) {
    return EmitResult::kDone;
  }
  if (result == Callback::TRAVERSE_CULL) {
    return EmitResult::kCull;
  }
  if (result == Callback::TRAVERSE_NEXT_KEY) {
    return EmitResult::kContinue;
  }
  result = callback->OnActualKey(key, key, 0);
  if (result == Callback::TRAVERSE_DONE) {
    return EmitResult::kDone;
  }
  if (result == Callback::TRAVERSE_CULL) {
    return EmitResult::kCull;
  }
  if (result == Callback::TRAVERSE_NEXT_KEY) {
    return EmitResult::kContinue;
  }
  for (auto iter = begin; iter != end; ++iter) {
    result = callback->OnToken(key, key, iter->token);
    if (result == Callback::TRAVERSE_DONE) {
      return EmitResult::kDone;
    }
    if (result == Callback::TRAVERSE_CULL) {
      return EmitResult::kCull;
    }
    if (result == Callback::TRAVERSE_NEXT_KEY) {
      break;
    }
  }
  return EmitResult::kContinue;
}

bool ProjectDictionarySnapshot::HasKey(absl::string_view key) const {
  const auto [begin, end] = EqualRange(key);
  return begin != end;
}

bool ProjectDictionarySnapshot::HasValue(absl::string_view value) const {
  return std::any_of(entries_.begin(), entries_.end(),
                     [value](const StoredEntry& entry) {
                       return entry.token.value == value;
                     });
}

void ProjectDictionarySnapshot::LookupExact(absl::string_view key,
                                            Callback* callback) const {
  const auto [begin, end] = EqualRange(key);
  EmitKey(begin, end, callback);
}

void ProjectDictionarySnapshot::LookupPredictive(absl::string_view key,
                                                 Callback* callback) const {
  auto iter = std::lower_bound(
      entries_.begin(), entries_.end(), key,
      [](const StoredEntry& entry, absl::string_view needle) {
        return entry.token.key < needle;
      });
  while (iter != entries_.end() && absl::StartsWith(iter->token.key, key)) {
    const auto [begin, end] = EqualRange(iter->token.key);
    const EmitResult result = EmitKey(begin, end, callback);
    if (result == EmitResult::kDone) {
      return;
    }
    if (result == EmitResult::kCull) {
      const absl::string_view culled_prefix = begin->token.key;
      iter = end;
      while (iter != entries_.end() &&
             absl::StartsWith(iter->token.key, culled_prefix)) {
        ++iter;
      }
      continue;
    }
    iter = end;
  }
}

void ProjectDictionarySnapshot::LookupPrefix(absl::string_view key,
                                             Callback* callback) const {
  // Byte offsets are safe here: an offset inside a UTF-8 scalar simply cannot
  // match a validated complete dictionary key.
  for (size_t length = 1; length <= key.size(); ++length) {
    const auto [begin, end] = EqualRange(key.substr(0, length));
    if (begin != end) {
      const EmitResult result = EmitKey(begin, end, callback);
      if (result == EmitResult::kDone || result == EmitResult::kCull) {
        return;
      }
    }
  }
}

ProjectDictionaryRegistry::PublishResult ProjectDictionaryRegistry::Publish(
    std::shared_ptr<const ProjectDictionarySnapshot> snapshot) {
  if (snapshot == nullptr) {
    return PublishResult::kRejectedNull;
  }
  absl::MutexLock lock(mutex_);
  if (secure_input_) {
    return PublishResult::kRejectedSecure;
  }
  if (latest_ == nullptr) {
    latest_ = std::move(snapshot);
    return PublishResult::kApplied;
  }
  if (snapshot->generation() < latest_->generation()) {
    return PublishResult::kRejectedStale;
  }
  if (snapshot->generation() == latest_->generation()) {
    if (snapshot->source_id() == latest_->source_id() &&
        snapshot->fingerprint() == latest_->fingerprint()) {
      return PublishResult::kUnchanged;
    }
    return PublishResult::kRejectedConflict;
  }
  latest_ = std::move(snapshot);
  return PublishResult::kApplied;
}

std::shared_ptr<const ProjectDictionarySnapshot>
ProjectDictionaryRegistry::PinForComposition() {
  absl::MutexLock lock(mutex_);
  pinned_ = secure_input_ ? nullptr : latest_;
  return pinned_;
}

void ProjectDictionaryRegistry::EndComposition() {
  absl::MutexLock lock(mutex_);
  pinned_.reset();
}

void ProjectDictionaryRegistry::SetSecureInput(bool secure) {
  absl::MutexLock lock(mutex_);
  secure_input_ = secure;
  if (secure) {
    // This registry is session-owned, so this purge cannot affect another
    // input context's latest or pinned generation.
    latest_.reset();
    pinned_.reset();
  }
}

void ProjectDictionaryRegistry::Purge() {
  absl::MutexLock lock(mutex_);
  latest_.reset();
  pinned_.reset();
}

bool ProjectDictionaryRegistry::secure_input() const {
  absl::MutexLock lock(mutex_);
  return secure_input_;
}

ProjectDictionaryRegistry::Status ProjectDictionaryRegistry::status() const {
  absl::MutexLock lock(mutex_);
  Status result;
  result.secure_input = secure_input_;
  if (latest_ != nullptr) {
    result.latest_generation = latest_->generation();
  }
  if (pinned_ != nullptr) {
    result.pinned_generation = pinned_->generation();
  }
  return result;
}

std::shared_ptr<const ProjectDictionarySnapshot>
ProjectDictionaryRegistry::latest() const {
  absl::MutexLock lock(mutex_);
  return latest_;
}

std::shared_ptr<const ProjectDictionarySnapshot>
ProjectDictionaryRegistry::pinned() const {
  absl::MutexLock lock(mutex_);
  return pinned_;
}

}  // namespace mozc::dictionary
