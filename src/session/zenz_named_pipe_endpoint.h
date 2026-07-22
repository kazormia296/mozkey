// Copyright 2026 The Mozkey Authors

#ifndef MOZC_SESSION_ZENZ_NAMED_PIPE_ENDPOINT_H_
#define MOZC_SESSION_ZENZ_NAMED_PIPE_ENDPOINT_H_

namespace mozc::session {

// Canonical Windows endpoint used by the scorer and checked against the client
// configuration default. A raw string keeps the separators unambiguous.
inline constexpr char kDefaultZenzNamedPipeName[] =
    R"(\\.\pipe\mozkey-ibg_zenz_scorer)";

}  // namespace mozc::session

#endif  // MOZC_SESSION_ZENZ_NAMED_PIPE_ENDPOINT_H_
