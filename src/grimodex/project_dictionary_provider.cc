// Copyright 2026 The Mozkey Authors

#include "grimodex/project_dictionary_provider.h"

#include <memory>
#include <mutex>
#include <string>
#include <utility>

#include "dictionary/project_dictionary.h"
#include "grimodex/project_dictionary_bridge.h"
#include "grimodex/protocol_v1.h"

namespace mozc::grimodex {

ProtocolV1ProjectDictionaryProvider::ProtocolV1ProjectDictionaryProvider(
    std::shared_ptr<ProtocolV1SnapshotPublisher> publisher,
    ProjectDictionaryPosIds pos_ids)
    : publisher_(std::move(publisher)), pos_ids_(pos_ids) {}

dictionary::ProjectDictionaryPublication
ProtocolV1ProjectDictionaryProvider::Reload() {
  // Keep publication order and the native cache update in one critical
  // section.  The publisher also serializes its file reads, but without this
  // outer lock an older bridge result could overwrite a newer cached result.
  std::lock_guard<std::mutex> lock(reload_mutex_);
  if (publisher_ == nullptr) {
    cached_.reset();
    return {.snapshot = nullptr, .clear = true};
  }

  const std::shared_ptr<const PublishedProtocolV1Snapshot> published =
      publisher_->Reload();

  // ProtocolV1SnapshotPublisher defines sequence as the semantic generation.
  // A timestamp/provenance-only rewrite intentionally retains that sequence;
  // reuse the prior indexed object so a same-generation, different raw digest
  // can never become a conflicting session publication.
  if (published != nullptr && published->snapshot != nullptr &&
      cached_ != nullptr && published->sequence == cached_->generation()) {
    return {.snapshot = cached_, .clear = false};
  }

  ProjectDictionaryBridgeResult result =
      BuildProjectDictionarySnapshot(published, pos_ids_);
  if (!result.ready()) {
    cached_.reset();
    return {.snapshot = nullptr, .clear = true};
  }

  cached_ = result.snapshot->dictionary;
  return {.snapshot = cached_, .clear = false};
}

std::shared_ptr<dictionary::ProjectDictionaryProviderInterface>
CreateProtocolV1ProjectDictionaryProvider(std::string root_path,
                                          ProjectDictionaryPosIds pos_ids) {
  auto reader =
      std::make_shared<SecureProtocolV1FileReader>(std::move(root_path));
  auto loader = std::make_shared<ProtocolV1Loader>(std::move(reader));
  auto publisher =
      std::make_shared<ProtocolV1SnapshotPublisher>(std::move(loader));
  return std::make_shared<ProtocolV1ProjectDictionaryProvider>(
      std::move(publisher), pos_ids);
}

}  // namespace mozc::grimodex
