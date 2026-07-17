// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_H_
#define MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_H_

#include <memory>
#include <mutex>
#include <string>

#include "dictionary/project_dictionary.h"
#include "grimodex/project_dictionary_bridge.h"
#include "grimodex/protocol_v1.h"

namespace mozc::grimodex {

// Linux Protocol v1 implementation of the platform-neutral provider contract.
// Reload revalidates the state boundary for every composition while caching
// the already indexed native snapshot for an unchanged semantic sequence.
class ProtocolV1ProjectDictionaryProvider final
    : public dictionary::ProjectDictionaryProviderInterface {
 public:
  ProtocolV1ProjectDictionaryProvider(
      std::shared_ptr<ProtocolV1SnapshotPublisher> publisher,
      ProjectDictionaryPosIds pos_ids);

  dictionary::ProjectDictionaryPublication Reload() override;

 private:
  std::mutex reload_mutex_;
  std::shared_ptr<ProtocolV1SnapshotPublisher> publisher_;
  const ProjectDictionaryPosIds pos_ids_;
  std::shared_ptr<const dictionary::ProjectDictionarySnapshot> cached_;
};

std::shared_ptr<dictionary::ProjectDictionaryProviderInterface>
CreateProtocolV1ProjectDictionaryProvider(std::string root_path,
                                          ProjectDictionaryPosIds pos_ids);

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_H_
