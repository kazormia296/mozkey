// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_PROTOCOL_V1_SECURE_READER_H_
#define MOZC_GRIMODEX_PROTOCOL_V1_SECURE_READER_H_

#include <cstddef>
#include <string>

#include "absl/status/statusor.h"
#include "absl/strings/string_view.h"
#include "grimodex/protocol_v1.h"

namespace mozc::grimodex {

// Linux production reader for the Protocol v1 filesystem contract.  The
// portable parser and publication logic depend only on ProtocolV1FileReader
// and deliberately do not imply filesystem runtime support on other systems.
class SecureProtocolV1FileReader final : public ProtocolV1FileReader {
 public:
  explicit SecureProtocolV1FileReader(std::string root_path);

  absl::StatusOr<VerifiedFileBytes> ReadState(size_t max_bytes) override;
  absl::StatusOr<VerifiedFileBytes> ReadProject(
      absl::string_view project_id, size_t max_bytes) override;

  const std::string &root_path() const { return root_path_; }

 private:
  std::string root_path_;
};

// Linux XDG path selection helper.  Supplying override_root makes integration
// and tests independent of the ambient process environment.
std::string ResolveProtocolV1Root(absl::string_view override_root,
                                  absl::string_view xdg_data_home,
                                  absl::string_view home_directory);

}  // namespace mozc::grimodex

#endif  // MOZC_GRIMODEX_PROTOCOL_V1_SECURE_READER_H_
