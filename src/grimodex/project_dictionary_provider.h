// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_H_
#define MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_H_

#include <memory>
#include <mutex>
#include <string>

#include "absl/strings/string_view.h"
#include "dictionary/project_dictionary.h"
#include "grimodex/project_dictionary_bridge.h"
#include "grimodex/protocol_v1.h"

namespace mozc::grimodex {

// Project snapshots contain unpublished story data, so the default scope is
// intentionally narrower than the normal Mozc conversion scope.  Widening to
// every application must be an explicit local configuration choice.
enum class ApplicationScopeMode {
  kOff,
  kGrimodexOnly,
  kAllApplications,
};

// Empty means the secure default (Grimodex only).  Unknown non-empty values
// fail closed to off instead of silently widening or accepting a typo.
ApplicationScopeMode ParseApplicationScopeMode(absl::string_view value);
bool AllowsApplication(ApplicationScopeMode mode,
                       absl::string_view program);

// Protocol v1 implementation of the platform-neutral provider contract.
// Reload revalidates the injected reader boundary for every composition while
// caching the already indexed native snapshot for an unchanged semantic
// sequence.  Platform integrations decide whether a filesystem-backed reader
// is available and construct the publisher separately.
class ProtocolV1ProjectDictionaryProvider final
    : public dictionary::ProjectDictionaryProviderInterface {
 public:
  ProtocolV1ProjectDictionaryProvider(
      std::shared_ptr<ProtocolV1SnapshotPublisher> publisher,
      ProjectDictionaryPosIds pos_ids,
      ApplicationScopeMode scope_mode =
          ApplicationScopeMode::kGrimodexOnly);

  bool AllowsApplication(absl::string_view program) const override;
  dictionary::ProjectDictionaryPublication Reload() override;

 private:
  std::mutex reload_mutex_;
  std::shared_ptr<ProtocolV1SnapshotPublisher> publisher_;
  const ProjectDictionaryPosIds pos_ids_;
  const ApplicationScopeMode scope_mode_;
  std::shared_ptr<const dictionary::ProjectDictionarySnapshot> cached_;
};

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_H_
