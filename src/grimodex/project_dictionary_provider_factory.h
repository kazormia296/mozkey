// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_FACTORY_H_
#define MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_FACTORY_H_

#include <memory>

#include "dictionary/project_dictionary.h"
#include "grimodex/project_dictionary_bridge.h"
#include "grimodex/project_dictionary_provider.h"
#include "grimodex/protocol_v1.h"

namespace mozc::grimodex {

// Constructs a Protocol v1 provider around a platform-owned reader.  Keeping
// filesystem selection outside this portable factory lets each desktop OS
// enforce its native path and handle-security contract without duplicating the
// parser, publisher, bridge, or provider state machine.
std::shared_ptr<dictionary::ProjectDictionaryProviderInterface>
CreateProtocolV1ProjectDictionaryProvider(
    std::shared_ptr<ProtocolV1FileReader> reader,
    ProjectDictionaryPosIds pos_ids, ApplicationScopeMode scope_mode);

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_PROJECT_DICTIONARY_PROVIDER_FACTORY_H_
