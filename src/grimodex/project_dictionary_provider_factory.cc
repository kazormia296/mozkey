// Copyright 2026 The Mozkey Authors

#include "grimodex/project_dictionary_provider_factory.h"

#include <memory>
#include <string>
#include <utility>

#include "dictionary/project_dictionary.h"
#include "grimodex/project_dictionary_bridge.h"
#include "grimodex/project_dictionary_provider.h"
#include "grimodex/protocol_v1.h"
#include "grimodex/protocol_v1_secure_reader.h"

namespace mozc::grimodex {

std::shared_ptr<dictionary::ProjectDictionaryProviderInterface>
CreateProtocolV1ProjectDictionaryProvider(std::string root_path,
                                          ProjectDictionaryPosIds pos_ids,
                                          ApplicationScopeMode scope_mode) {
  auto reader =
      std::make_shared<SecureProtocolV1FileReader>(std::move(root_path));
  auto loader = std::make_shared<ProtocolV1Loader>(std::move(reader));
  auto publisher =
      std::make_shared<ProtocolV1SnapshotPublisher>(std::move(loader));
  return std::make_shared<ProtocolV1ProjectDictionaryProvider>(
      std::move(publisher), pos_ids, scope_mode);
}

}  // namespace mozc::grimodex
