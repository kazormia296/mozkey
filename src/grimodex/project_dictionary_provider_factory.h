// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_FACTORY_H_
#define MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_FACTORY_H_

#include <memory>
#include <string>

#include "dictionary/project_dictionary.h"
#include "grimodex/project_dictionary_bridge.h"
#include "grimodex/project_dictionary_provider.h"

namespace mozc::grimodex {

// Constructs the Linux filesystem-backed Protocol v1 provider.  Non-Linux
// engines intentionally leave the provider disabled until their runtime
// filesystem contract is designed and implemented.
std::shared_ptr<dictionary::ProjectDictionaryProviderInterface>
CreateProtocolV1ProjectDictionaryProvider(std::string root_path,
                                          ProjectDictionaryPosIds pos_ids,
                                          ApplicationScopeMode scope_mode);

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_FACTORY_H_
