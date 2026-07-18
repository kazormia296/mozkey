// Copyright 2026 The Mozkey Authors

#ifndef MOZC_GRIMODEX_PROTOCOL_V1_WINDOWS_SECURE_READER_H_
#define MOZC_GRIMODEX_PROTOCOL_V1_WINDOWS_SECURE_READER_H_

#if defined(_WIN32)

#include <windows.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/string_view.h"
#include "grimodex/protocol_v1.h"

namespace mozc::grimodex {

// Windows production reader for the Protocol v1 filesystem contract.  Paths
// are UTF-8 at the boundary and are opened with Win32 handles that permit the
// producer's atomic-replace publication sequence.
class WindowsSecureProtocolV1FileReader final : public ProtocolV1FileReader {
 public:
  explicit WindowsSecureProtocolV1FileReader(std::string root_path);

  absl::StatusOr<VerifiedFileBytes> ReadState(size_t max_bytes) override;
  absl::StatusOr<VerifiedFileBytes> ReadProject(
      absl::string_view project_id, size_t max_bytes) override;

  const std::string &root_path() const { return root_path_; }

 private:
  std::string root_path_;
};

// Resolves a UTF-8 override or the current process's %APPDATA% directory.
// The two-argument overload makes environment selection deterministic in
// tests and embedding applications.
absl::StatusOr<std::string> ResolveWindowsProtocolV1Root(
    absl::string_view override_root);
absl::StatusOr<std::string> ResolveWindowsProtocolV1Root(
    absl::string_view override_root,
    absl::string_view app_data_directory);

namespace protocol_v1_windows_internal {

// Stable fields used to reject an in-place mutation during a read.  This is
// exposed only so the Windows-only contract test can cover every comparison.
struct FileVersion {
  uint64_t volume_serial_number = 0;
  std::array<uint8_t, 16> file_id = {};
  uint64_t size = 0;
  int64_t last_write_time = 0;
  int64_t change_time = 0;
  uint32_t number_of_links = 0;
};

bool SameFileVersion(const FileVersion &first, const FileVersion &second);
bool AccessMaskAllowsMutation(uint32_t access_mask);

// Validates the owner and integrity-sensitive DACL entries of an already-open
// filesystem object.  The handle must have READ_CONTROL access.
absl::Status ValidateCurrentUserSecurity(HANDLE handle,
                                         absl::string_view label);

}  // namespace protocol_v1_windows_internal
}  // namespace mozc::grimodex

#endif  // defined(_WIN32)

#endif  // MOZC_GRIMODEX_PROTOCOL_V1_WINDOWS_SECURE_READER_H_
