// Copyright 2026 The Mozkey Authors

#include "grimodex/project_dictionary_provider.h"

#include <memory>
#include <mutex>
#include <string>
#include <utility>

#include "absl/strings/ascii.h"
#include "absl/strings/string_view.h"
#include "dictionary/project_dictionary.h"
#include "grimodex/project_dictionary_bridge.h"
#include "grimodex/protocol_v1.h"

namespace mozc::grimodex {

ApplicationScopeMode ParseApplicationScopeMode(absl::string_view value) {
  const std::string normalized =
      absl::AsciiStrToLower(std::string(absl::StripAsciiWhitespace(value)));
  if (normalized.empty() || normalized == "grimodex" ||
      normalized == "grimodex-only") {
    return ApplicationScopeMode::kGrimodexOnly;
  }
  if (normalized == "all" || normalized == "all-applications") {
    return ApplicationScopeMode::kAllApplications;
  }
  if (normalized == "off") {
    return ApplicationScopeMode::kOff;
  }
  return ApplicationScopeMode::kOff;
}

bool AllowsApplication(ApplicationScopeMode mode,
                       absl::string_view program) {
  if (mode == ApplicationScopeMode::kOff) {
    return false;
  }
  if (mode == ApplicationScopeMode::kAllApplications) {
    return true;
  }
  const std::string normalized =
      absl::AsciiStrToLower(std::string(absl::StripAsciiWhitespace(program)));
  return normalized == "grimodex" ||
         normalized == "com.miyakey.grimodex";
}

ProtocolV1ProjectDictionaryProvider::ProtocolV1ProjectDictionaryProvider(
    std::shared_ptr<ProtocolV1SnapshotPublisher> publisher,
    ProjectDictionaryPosIds pos_ids, ApplicationScopeMode scope_mode)
    : publisher_(std::move(publisher)),
      pos_ids_(pos_ids),
      scope_mode_(scope_mode) {}

bool ProtocolV1ProjectDictionaryProvider::AllowsApplication(
    absl::string_view program) const {
  return grimodex::AllowsApplication(scope_mode_, program);
}

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

}  // namespace mozc::grimodex
