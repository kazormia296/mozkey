// Copyright 2026 The Mozkey Authors

#include "grimodex/protocol_v1_windows_secure_reader.h"

#if defined(_WIN32)

#include <aclapi.h>
#include <shlobj.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/string_view.h"
#include "grimodex/protocol_v1.h"

namespace mozc::grimodex {
namespace {

using protocol_v1_windows_internal::FileVersion;

constexpr wchar_t kProtocolV1RelativeRoot[] =
    L"com.miyakey.grimodex\\ime";

class UniqueHandle final {
 public:
  explicit UniqueHandle(HANDLE handle = INVALID_HANDLE_VALUE)
      : handle_(handle) {}
  ~UniqueHandle() {
    if (valid()) {
      ::CloseHandle(handle_);
    }
  }

  UniqueHandle(const UniqueHandle &) = delete;
  UniqueHandle &operator=(const UniqueHandle &) = delete;
  UniqueHandle(UniqueHandle &&other) noexcept
      : handle_(std::exchange(other.handle_, INVALID_HANDLE_VALUE)) {}
  UniqueHandle &operator=(UniqueHandle &&other) noexcept {
    if (this != &other) {
      if (valid()) {
        ::CloseHandle(handle_);
      }
      handle_ = std::exchange(other.handle_, INVALID_HANDLE_VALUE);
    }
    return *this;
  }

  bool valid() const {
    return handle_ != nullptr && handle_ != INVALID_HANDLE_VALUE;
  }
  HANDLE get() const { return handle_; }

 private:
  HANDLE handle_;
};

class UniqueLocalMemory final {
 public:
  explicit UniqueLocalMemory(HLOCAL memory = nullptr) : memory_(memory) {}
  ~UniqueLocalMemory() {
    if (memory_ != nullptr) {
      ::LocalFree(memory_);
    }
  }

  UniqueLocalMemory(const UniqueLocalMemory &) = delete;
  UniqueLocalMemory &operator=(const UniqueLocalMemory &) = delete;

 private:
  HLOCAL memory_;
};

class UniqueCoTaskMemory final {
 public:
  explicit UniqueCoTaskMemory(PWSTR memory = nullptr) : memory_(memory) {}
  ~UniqueCoTaskMemory() {
    if (memory_ != nullptr) {
      ::CoTaskMemFree(memory_);
    }
  }

  UniqueCoTaskMemory(const UniqueCoTaskMemory &) = delete;
  UniqueCoTaskMemory &operator=(const UniqueCoTaskMemory &) = delete;

  PWSTR get() const { return memory_; }

 private:
  PWSTR memory_;
};

struct ParsedRootPath final {
  // Every entry is an extended-length absolute directory path.  The first is
  // the local drive root and the last is the configured IME root.
  std::vector<std::wstring> prefixes;
};

struct SecureDirectory final {
  UniqueHandle handle;
  FileVersion version;
  std::wstring path;
};

absl::Status WindowsError(absl::string_view operation, DWORD error) {
  const std::string message =
      absl::StrCat(operation, " failed (win32_error=", error, ")");
  switch (error) {
    case ERROR_FILE_NOT_FOUND:
    case ERROR_PATH_NOT_FOUND:
    case ERROR_ENVVAR_NOT_FOUND:
      return absl::NotFoundError(message);
    case ERROR_ACCESS_DENIED:
    case ERROR_SHARING_VIOLATION:
    case ERROR_LOCK_VIOLATION:
    case ERROR_PRIVILEGE_NOT_HELD:
      return absl::PermissionDeniedError(message);
    case ERROR_INVALID_NAME:
    case ERROR_BAD_PATHNAME:
    case ERROR_FILENAME_EXCED_RANGE:
      return absl::InvalidArgumentError(message);
    default:
      return absl::FailedPreconditionError(message);
  }
}

absl::StatusOr<std::wstring> Utf8ToWideStrict(absl::string_view input,
                                               absl::string_view label) {
  if (input.empty()) {
    return absl::InvalidArgumentError(absl::StrCat(label, " is empty"));
  }
  if (input.find('\0') != absl::string_view::npos) {
    return absl::InvalidArgumentError(
        absl::StrCat(label, " contains an embedded NUL"));
  }
  if (input.size() > static_cast<size_t>((std::numeric_limits<int>::max)())) {
    return absl::InvalidArgumentError(absl::StrCat(label, " is too long"));
  }
  const int input_size = static_cast<int>(input.size());
  const int required = ::MultiByteToWideChar(
      CP_UTF8, MB_ERR_INVALID_CHARS, input.data(), input_size, nullptr, 0);
  if (required <= 0) {
    return absl::InvalidArgumentError(
        absl::StrCat(label, " is not valid UTF-8"));
  }
  std::wstring result(static_cast<size_t>(required), L'\0');
  if (::MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input.data(),
                            input_size, result.data(), required) != required) {
    return WindowsError(absl::StrCat("convert ", label, " to UTF-16"),
                        ::GetLastError());
  }
  return result;
}

absl::StatusOr<std::string> WideToUtf8Strict(absl::string_view label,
                                             const std::wstring &input) {
  if (input.empty()) {
    return absl::InvalidArgumentError(absl::StrCat(label, " is empty"));
  }
  if (input.size() >
      static_cast<size_t>((std::numeric_limits<int>::max)())) {
    return absl::InvalidArgumentError(absl::StrCat(label, " is too long"));
  }
  const int input_size = static_cast<int>(input.size());
  const int required = ::WideCharToMultiByte(
      CP_UTF8, WC_ERR_INVALID_CHARS, input.data(), input_size, nullptr, 0,
      nullptr, nullptr);
  if (required <= 0) {
    return WindowsError(absl::StrCat("convert ", label, " to UTF-8"),
                        ::GetLastError());
  }
  std::string result(static_cast<size_t>(required), '\0');
  if (::WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, input.data(),
                            input_size, result.data(), required, nullptr,
                            nullptr) != required) {
    return WindowsError(absl::StrCat("convert ", label, " to UTF-8"),
                        ::GetLastError());
  }
  return result;
}

bool IsAsciiAlpha(wchar_t value) {
  return (value >= L'A' && value <= L'Z') ||
         (value >= L'a' && value <= L'z');
}

bool IsReservedDeviceName(std::wstring component) {
  const size_t dot = component.find(L'.');
  if (dot != std::wstring::npos) {
    component.resize(dot);
  }
  for (wchar_t &value : component) {
    if (value >= L'a' && value <= L'z') {
      value -= L'a' - L'A';
    }
  }
  if (component == L"CON" || component == L"PRN" ||
      component == L"AUX" || component == L"NUL") {
    return true;
  }
  if (component.size() == 4 &&
      (component.compare(0, 3, L"COM") == 0 ||
       component.compare(0, 3, L"LPT") == 0) &&
      component[3] >= L'1' && component[3] <= L'9') {
    return true;
  }
  return false;
}

absl::Status ValidatePathComponent(std::wstring_view component) {
  if (component.empty()) {
    return absl::InvalidArgumentError(
        "Grimodex root contains an empty path component");
  }
  if (component == L"." || component == L"..") {
    return absl::InvalidArgumentError(
        "Grimodex root contains a traversal component");
  }
  if (component.back() == L'.' || component.back() == L' ') {
    return absl::InvalidArgumentError(
        "Grimodex root contains a non-canonical path component");
  }
  for (const wchar_t value : component) {
    if (value < 0x20 || value == L'"' || value == L'<' || value == L'>' ||
        value == L':' || value == L'|' || value == L'?' || value == L'*' ||
        value == L'\\' || value == L'/') {
      return absl::InvalidArgumentError(
          "Grimodex root contains an invalid path character");
    }
  }
  if (IsReservedDeviceName(std::wstring(component))) {
    return absl::InvalidArgumentError(
        "Grimodex root contains a reserved device name");
  }
  return absl::OkStatus();
}

absl::StatusOr<ParsedRootPath> ParseRootPath(absl::string_view utf8_path) {
  absl::StatusOr<std::wstring> converted =
      Utf8ToWideStrict(utf8_path, "Grimodex root");
  if (!converted.ok()) {
    return converted.status();
  }
  std::wstring path = std::move(*converted);
  std::replace(path.begin(), path.end(), L'/', L'\\');
  if (path.back() == L'\\') {
    return absl::InvalidArgumentError(
        "Grimodex root must not have a trailing separator");
  }
  if (path.rfind(L"\\\\?\\", 0) == 0 ||
      path.rfind(L"\\\\.\\", 0) == 0) {
    return absl::InvalidArgumentError(
        "Grimodex root must not use a device namespace");
  }

  ParsedRootPath result;
  std::wstring prefix;
  size_t component_begin = 0;
  if (path.size() >= 3 && IsAsciiAlpha(path[0]) && path[1] == L':' &&
      path[2] == L'\\') {
    const std::wstring drive_root = path.substr(0, 3);
    const UINT drive_type = ::GetDriveTypeW(drive_root.c_str());
    if (!protocol_v1_windows_internal::IsFixedLocalDriveType(drive_type)) {
      return absl::InvalidArgumentError(absl::StrCat(
          "Grimodex root must be on a fixed local drive (drive_type=",
          drive_type, ")"));
    }
    prefix = L"\\\\?\\" + path.substr(0, 3);
    result.prefixes.push_back(prefix);
    component_begin = 3;
  } else if (path.rfind(L"\\\\", 0) == 0) {
    return absl::InvalidArgumentError(
        "UNC Grimodex roots are not allowed in local-only mode");
  } else {
    return absl::InvalidArgumentError(
        "Grimodex root must be an absolute local drive path");
  }

  size_t component_count = 0;
  while (component_begin < path.size()) {
    const size_t end = path.find(L'\\', component_begin);
    const size_t length = end == std::wstring::npos
                              ? path.size() - component_begin
                              : end - component_begin;
    const std::wstring component = path.substr(component_begin, length);
    if (absl::Status status = ValidatePathComponent(component); !status.ok()) {
      return status;
    }
    if (prefix.back() != L'\\') {
      prefix.append(L"\\");
    }
    prefix.append(component);
    result.prefixes.push_back(prefix);
    ++component_count;
    if (end == std::wstring::npos) {
      break;
    }
    component_begin = end + 1;
  }
  if (component_count == 0) {
    return absl::InvalidArgumentError(
        "filesystem volume cannot be an IME root");
  }
  return result;
}

absl::StatusOr<FileVersion> GetFileVersion(HANDLE handle,
                                           absl::string_view label) {
  FILE_STANDARD_INFO standard = {};
  if (!::GetFileInformationByHandleEx(handle, FileStandardInfo, &standard,
                                      sizeof(standard))) {
    return WindowsError(absl::StrCat("query ", label, " standard metadata"),
                        ::GetLastError());
  }
  FILE_BASIC_INFO basic = {};
  if (!::GetFileInformationByHandleEx(handle, FileBasicInfo, &basic,
                                      sizeof(basic))) {
    return WindowsError(absl::StrCat("query ", label, " basic metadata"),
                        ::GetLastError());
  }

  FileVersion version;
  version.size = standard.EndOfFile.QuadPart < 0
                     ? (std::numeric_limits<uint64_t>::max)()
                     : static_cast<uint64_t>(standard.EndOfFile.QuadPart);
  version.last_write_time = basic.LastWriteTime.QuadPart;
  version.change_time = basic.ChangeTime.QuadPart;
  version.number_of_links = standard.NumberOfLinks;

  FILE_ID_INFO id = {};
  if (::GetFileInformationByHandleEx(handle, FileIdInfo, &id, sizeof(id))) {
    version.volume_serial_number = id.VolumeSerialNumber;
    std::memcpy(version.file_id.data(), id.FileId.Identifier,
                version.file_id.size());
    return version;
  }

  // FileIdInfo is unavailable on a few legacy and remote filesystems.  The
  // legacy volume/file-index tuple still identifies the already-open object.
  BY_HANDLE_FILE_INFORMATION legacy = {};
  if (!::GetFileInformationByHandle(handle, &legacy)) {
    return WindowsError(absl::StrCat("query ", label, " file identity"),
                        ::GetLastError());
  }
  version.volume_serial_number = legacy.dwVolumeSerialNumber;
  const uint64_t file_index =
      (static_cast<uint64_t>(legacy.nFileIndexHigh) << 32) |
      legacy.nFileIndexLow;
  std::memcpy(version.file_id.data(), &file_index, sizeof(file_index));
  return version;
}

bool SameFileIdentity(const FileVersion &first, const FileVersion &second) {
  return first.volume_serial_number == second.volume_serial_number &&
         first.file_id == second.file_id;
}

absl::StatusOr<UniqueHandle> OpenPath(const std::wstring &path, DWORD access,
                                      bool allow_directory,
                                      absl::string_view label) {
  DWORD flags = FILE_FLAG_OPEN_REPARSE_POINT;
  if (allow_directory) {
    flags |= FILE_FLAG_BACKUP_SEMANTICS;
  }
  HANDLE raw = ::CreateFileW(
      path.c_str(), access,
      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
      OPEN_EXISTING, flags, nullptr);
  if (raw == INVALID_HANDLE_VALUE) {
    return WindowsError(absl::StrCat("open ", label), ::GetLastError());
  }
  return UniqueHandle(raw);
}

absl::Status ValidateDirectoryHandle(HANDLE handle, absl::string_view label,
                                     bool validate_security) {
  FILE_BASIC_INFO basic = {};
  if (!::GetFileInformationByHandleEx(handle, FileBasicInfo, &basic,
                                      sizeof(basic))) {
    return WindowsError(absl::StrCat("query ", label, " attributes"),
                        ::GetLastError());
  }
  if ((basic.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " is a reparse point"));
  }
  FILE_STANDARD_INFO standard = {};
  if (!::GetFileInformationByHandleEx(handle, FileStandardInfo, &standard,
                                      sizeof(standard))) {
    return WindowsError(absl::StrCat("query ", label, " type"),
                        ::GetLastError());
  }
  if (!standard.Directory) {
    return absl::FailedPreconditionError(
        absl::StrCat(label, " is not a directory"));
  }
  if (validate_security) {
    return protocol_v1_windows_internal::ValidateCurrentUserSecurity(handle,
                                                                      label);
  }
  return absl::OkStatus();
}

absl::StatusOr<SecureDirectory> OpenSecureRoot(absl::string_view utf8_path) {
  absl::StatusOr<ParsedRootPath> parsed = ParseRootPath(utf8_path);
  if (!parsed.ok()) {
    return parsed.status();
  }

  std::vector<UniqueHandle> chain;
  chain.reserve(parsed->prefixes.size());
  for (size_t index = 0; index < parsed->prefixes.size(); ++index) {
    const bool is_final = index + 1 == parsed->prefixes.size();
    const DWORD access = FILE_READ_ATTRIBUTES | (is_final ? READ_CONTROL : 0);
    absl::StatusOr<UniqueHandle> handle = OpenPath(
        parsed->prefixes[index], access, true,
        is_final ? "Grimodex root" : "Grimodex root ancestor");
    if (!handle.ok()) {
      return handle.status();
    }
    if (absl::Status status = ValidateDirectoryHandle(
            handle->get(),
            is_final ? "Grimodex root" : "Grimodex root ancestor", is_final);
        !status.ok()) {
      return status;
    }
    chain.push_back(std::move(*handle));
  }

  absl::StatusOr<FileVersion> version =
      GetFileVersion(chain.back().get(), "Grimodex root");
  if (!version.ok()) {
    return version.status();
  }
  UniqueHandle final = std::move(chain.back());
  return SecureDirectory{.handle = std::move(final),
                         .version = *version,
                         .path = parsed->prefixes.back()};
}

absl::StatusOr<SecureDirectory> OpenSecureChildDirectory(
    const SecureDirectory &parent, std::wstring_view name,
    absl::string_view label) {
  std::wstring path = parent.path;
  path.append(L"\\");
  path.append(name);
  absl::StatusOr<UniqueHandle> handle =
      OpenPath(path, FILE_READ_ATTRIBUTES | READ_CONTROL, true, label);
  if (!handle.ok()) {
    return handle.status();
  }
  if (absl::Status status =
          ValidateDirectoryHandle(handle->get(), label, true);
      !status.ok()) {
    return status;
  }
  absl::StatusOr<FileVersion> version = GetFileVersion(handle->get(), label);
  if (!version.ok()) {
    return version.status();
  }
  return SecureDirectory{.handle = std::move(*handle),
                         .version = *version,
                         .path = std::move(path)};
}

absl::Status VerifyDirectoryPathIdentity(const SecureDirectory &directory,
                                         absl::string_view label) {
  absl::StatusOr<UniqueHandle> current =
      OpenPath(directory.path, FILE_READ_ATTRIBUTES | READ_CONTROL, true,
               label);
  if (!current.ok()) {
    return current.status();
  }
  if (absl::Status status =
          ValidateDirectoryHandle(current->get(), label, true);
      !status.ok()) {
    return status;
  }
  absl::StatusOr<FileVersion> version = GetFileVersion(current->get(), label);
  if (!version.ok()) {
    return version.status();
  }
  if (!SameFileIdentity(directory.version, *version)) {
    return absl::AbortedError(
        absl::StrCat(label, " changed while a snapshot was being read"));
  }
  return protocol_v1_windows_internal::ValidateCurrentUserSecurity(
      directory.handle.get(), label);
}

absl::Status ValidateFileHandle(HANDLE handle, absl::string_view label,
                                size_t max_bytes) {
  if (::GetFileType(handle) != FILE_TYPE_DISK) {
    return absl::FailedPreconditionError(
        absl::StrCat(label, " is not a disk file"));
  }
  FILE_BASIC_INFO basic = {};
  if (!::GetFileInformationByHandleEx(handle, FileBasicInfo, &basic,
                                      sizeof(basic))) {
    return WindowsError(absl::StrCat("query ", label, " attributes"),
                        ::GetLastError());
  }
  if ((basic.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " is a reparse point"));
  }
  FILE_STANDARD_INFO standard = {};
  if (!::GetFileInformationByHandleEx(handle, FileStandardInfo, &standard,
                                      sizeof(standard))) {
    return WindowsError(absl::StrCat("query ", label, " type and size"),
                        ::GetLastError());
  }
  if (standard.Directory) {
    return absl::FailedPreconditionError(
        absl::StrCat(label, " is not a regular file"));
  }
  if (standard.NumberOfLinks != 1) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " must have exactly one hard link"));
  }
  if (standard.EndOfFile.QuadPart < 0 ||
      static_cast<uint64_t>(standard.EndOfFile.QuadPart) > max_bytes ||
      static_cast<uint64_t>(standard.EndOfFile.QuadPart) >
          (std::numeric_limits<size_t>::max)()) {
    return absl::ResourceExhaustedError(
        absl::StrCat(label, " exceeds the Protocol v1 byte limit"));
  }
  return protocol_v1_windows_internal::ValidateCurrentUserSecurity(handle,
                                                                    label);
}

absl::StatusOr<VerifiedFileBytes> ReadSecureFile(
    const SecureDirectory &directory, std::wstring_view filename,
    absl::string_view label, size_t max_bytes) {
  std::wstring path = directory.path;
  path.append(L"\\");
  path.append(filename);
  absl::StatusOr<UniqueHandle> file =
      OpenPath(path, GENERIC_READ | READ_CONTROL,
               /*allow_directory=*/true, label);
  if (!file.ok()) {
    return file.status();
  }
  if (absl::Status status =
          ValidateFileHandle(file->get(), label, max_bytes);
      !status.ok()) {
    return status;
  }
  absl::StatusOr<FileVersion> before = GetFileVersion(file->get(), label);
  if (!before.ok()) {
    return before.status();
  }

  std::string bytes(static_cast<size_t>(before->size), '\0');
  size_t offset = 0;
  while (offset < bytes.size()) {
    const size_t remaining = bytes.size() - offset;
    const DWORD request = remaining > (std::numeric_limits<DWORD>::max)()
                              ? (std::numeric_limits<DWORD>::max)()
                              : static_cast<DWORD>(remaining);
    DWORD count = 0;
    if (!::ReadFile(file->get(), bytes.data() + offset, request, &count,
                    nullptr)) {
      return WindowsError(absl::StrCat("read ", label), ::GetLastError());
    }
    if (count == 0) {
      return absl::AbortedError(
          absl::StrCat(label, " shrank while it was being read"));
    }
    offset += count;
  }

  char extra = 0;
  DWORD extra_count = 0;
  if (!::ReadFile(file->get(), &extra, 1, &extra_count, nullptr)) {
    return WindowsError(absl::StrCat("probe ", label, " after read"),
                        ::GetLastError());
  }
  absl::StatusOr<FileVersion> after = GetFileVersion(file->get(), label);
  if (!after.ok()) {
    return after.status();
  }
  if (extra_count != 0 && after->size > max_bytes) {
    return absl::ResourceExhaustedError(
        absl::StrCat(label, " grew beyond the Protocol v1 byte limit"));
  }
  if (extra_count != 0 ||
      !protocol_v1_windows_internal::SameFileVersion(*before, *after)) {
    return absl::AbortedError(
        absl::StrCat(label, " changed while it was being read"));
  }
  return VerifiedFileBytes::FromBytes(std::move(bytes));
}

bool IsSafeIdentifier(absl::string_view value, size_t maximum) {
  if (value.empty() || value.size() > maximum) {
    return false;
  }
  for (const unsigned char byte : value) {
    const bool alphabetic = (byte >= 'A' && byte <= 'Z') ||
                            (byte >= 'a' && byte <= 'z');
    if (!alphabetic && !(byte >= '0' && byte <= '9') && byte != '-' &&
        byte != '_') {
      return false;
    }
  }
  return true;
}

absl::StatusOr<std::wstring> ReadRoamingAppDataKnownFolder() {
  PWSTR raw_path = nullptr;
  const HRESULT result = ::SHGetKnownFolderPath(
      FOLDERID_RoamingAppData, KF_FLAG_DEFAULT, nullptr, &raw_path);
  UniqueCoTaskMemory path(raw_path);
  if (FAILED(result)) {
    return absl::FailedPreconditionError(absl::StrCat(
        "resolve FOLDERID_RoamingAppData failed (hresult=",
        static_cast<uint32_t>(result), ")"));
  }
  if (path.get() == nullptr || path.get()[0] == L'\0') {
    return absl::FailedPreconditionError(
        "FOLDERID_RoamingAppData resolved to an empty path");
  }
  return std::wstring(path.get());
}

}  // namespace

namespace protocol_v1_windows_internal {

bool IsFixedLocalDriveType(uint32_t drive_type) {
  return drive_type == DRIVE_FIXED;
}

bool SameFileVersion(const FileVersion &first, const FileVersion &second) {
  return first.volume_serial_number == second.volume_serial_number &&
         first.file_id == second.file_id && first.size == second.size &&
         first.last_write_time == second.last_write_time &&
         first.change_time == second.change_time &&
         first.number_of_links == second.number_of_links;
}

bool AccessMaskAllowsMutation(uint32_t access_mask) {
  constexpr uint32_t kMutationRights =
      GENERIC_WRITE | GENERIC_ALL | DELETE | WRITE_DAC | WRITE_OWNER |
      FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_WRITE_EA |
      FILE_WRITE_ATTRIBUTES | FILE_DELETE_CHILD;
  return (access_mask & kMutationRights) != 0;
}

absl::Status ValidateCurrentUserSecurity(HANDLE handle,
                                         absl::string_view label) {
  UniqueHandle token;
  HANDLE raw_token = nullptr;
  if (!::OpenProcessToken(::GetCurrentProcess(), TOKEN_QUERY, &raw_token)) {
    return WindowsError("open current process token", ::GetLastError());
  }
  token = UniqueHandle(raw_token);

  DWORD token_size = 0;
  ::GetTokenInformation(token.get(), TokenUser, nullptr, 0, &token_size);
  if (token_size == 0) {
    return WindowsError("query current user SID size", ::GetLastError());
  }
  std::vector<uint8_t> token_buffer(token_size);
  if (!::GetTokenInformation(token.get(), TokenUser, token_buffer.data(),
                             token_size, &token_size)) {
    return WindowsError("query current user SID", ::GetLastError());
  }
  const PSID current_user =
      reinterpret_cast<TOKEN_USER *>(token_buffer.data())->User.Sid;
  if (!::IsValidSid(current_user)) {
    return absl::FailedPreconditionError("current process has an invalid SID");
  }

  PSID owner = nullptr;
  PACL dacl = nullptr;
  PSECURITY_DESCRIPTOR descriptor = nullptr;
  const DWORD security_error = ::GetSecurityInfo(
      handle, SE_FILE_OBJECT,
      OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION, &owner, nullptr,
      &dacl, nullptr, &descriptor);
  if (security_error != ERROR_SUCCESS) {
    return WindowsError(absl::StrCat("query ", label, " security"),
                        security_error);
  }
  UniqueLocalMemory descriptor_owner(descriptor);
  if (owner == nullptr || !::IsValidSid(owner) ||
      !::EqualSid(owner, current_user)) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " is not owned by the current user"));
  }
  if (dacl == nullptr || !::IsValidAcl(dacl)) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " has an absent or invalid DACL"));
  }

  std::array<DWORD, SECURITY_MAX_SID_SIZE / sizeof(DWORD)> system_storage = {};
  DWORD system_size = sizeof(system_storage);
  if (!::CreateWellKnownSid(WinLocalSystemSid, nullptr, system_storage.data(),
                            &system_size)) {
    return WindowsError("create LocalSystem SID", ::GetLastError());
  }
  std::array<DWORD, SECURITY_MAX_SID_SIZE / sizeof(DWORD)>
      administrators_storage = {};
  DWORD administrators_size = sizeof(administrators_storage);
  if (!::CreateWellKnownSid(WinBuiltinAdministratorsSid, nullptr,
                            administrators_storage.data(),
                            &administrators_size)) {
    return WindowsError("create Administrators SID", ::GetLastError());
  }

  for (DWORD index = 0; index < dacl->AceCount; ++index) {
    void *raw_ace = nullptr;
    if (!::GetAce(dacl, index, &raw_ace) || raw_ace == nullptr) {
      return WindowsError(absl::StrCat("read ", label, " DACL entry"),
                          ::GetLastError());
    }
    const ACE_HEADER *header = static_cast<const ACE_HEADER *>(raw_ace);
    if ((header->AceFlags & INHERIT_ONLY_ACE) != 0) {
      continue;
    }
    if (header->AceType != ACCESS_ALLOWED_ACE_TYPE) {
      if (header->AceType == ACCESS_ALLOWED_OBJECT_ACE_TYPE ||
          header->AceType == ACCESS_ALLOWED_CALLBACK_ACE_TYPE ||
          header->AceType == ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE) {
        return absl::PermissionDeniedError(
            absl::StrCat(label, " has an unsupported access-allowed ACE"));
      }
      continue;
    }
    if (header->AceSize <
        offsetof(ACCESS_ALLOWED_ACE, SidStart) + sizeof(DWORD)) {
      return absl::PermissionDeniedError(
          absl::StrCat(label, " has a truncated access-allowed ACE"));
    }
    const ACCESS_ALLOWED_ACE *ace =
        static_cast<const ACCESS_ALLOWED_ACE *>(raw_ace);
    const PSID trustee = const_cast<DWORD *>(&ace->SidStart);
    if (!::IsValidSid(trustee)) {
      return absl::PermissionDeniedError(
          absl::StrCat(label, " has an invalid DACL trustee"));
    }
    const bool trusted =
        ::EqualSid(trustee, current_user) ||
        ::EqualSid(trustee, system_storage.data()) ||
        ::EqualSid(trustee, administrators_storage.data());
    if (!trusted && AccessMaskAllowsMutation(ace->Mask)) {
      return absl::PermissionDeniedError(absl::StrCat(
          label, " grants mutation rights to an untrusted principal"));
    }
  }
  return absl::OkStatus();
}

}  // namespace protocol_v1_windows_internal

WindowsSecureProtocolV1FileReader::WindowsSecureProtocolV1FileReader(
    std::string root_path)
    : root_path_(std::move(root_path)) {}

absl::StatusOr<VerifiedFileBytes>
WindowsSecureProtocolV1FileReader::ReadState(size_t max_bytes) {
  absl::StatusOr<SecureDirectory> root = OpenSecureRoot(root_path_);
  if (!root.ok()) {
    return root.status();
  }
  absl::StatusOr<VerifiedFileBytes> result =
      ReadSecureFile(*root, L"state.json", "state.json", max_bytes);
  if (!result.ok()) {
    return result.status();
  }
  if (absl::Status status =
          VerifyDirectoryPathIdentity(*root, "Grimodex root");
      !status.ok()) {
    return status;
  }
  return result;
}

absl::StatusOr<VerifiedFileBytes>
WindowsSecureProtocolV1FileReader::ReadProject(absl::string_view project_id,
                                               size_t max_bytes) {
  if (!IsSafeIdentifier(project_id, ProtocolV1Limits::kProjectIdScalars)) {
    return absl::InvalidArgumentError("unsafe Grimodex project ID");
  }
  absl::StatusOr<SecureDirectory> root = OpenSecureRoot(root_path_);
  if (!root.ok()) {
    return root.status();
  }
  absl::StatusOr<SecureDirectory> projects =
      OpenSecureChildDirectory(*root, L"projects", "Grimodex projects directory");
  if (!projects.ok()) {
    return projects.status();
  }
  absl::StatusOr<std::wstring> project_id_wide =
      Utf8ToWideStrict(project_id, "Grimodex project ID");
  if (!project_id_wide.ok()) {
    return project_id_wide.status();
  }
  const std::wstring filename = *project_id_wide + L".json";
  absl::StatusOr<VerifiedFileBytes> result = ReadSecureFile(
      *projects, filename, "Grimodex project snapshot", max_bytes);
  if (!result.ok()) {
    return result.status();
  }
  if (absl::Status status = VerifyDirectoryPathIdentity(
          *projects, "Grimodex projects directory");
      !status.ok()) {
    return status;
  }
  if (absl::Status status =
          VerifyDirectoryPathIdentity(*root, "Grimodex root");
      !status.ok()) {
    return status;
  }
  return result;
}

absl::StatusOr<std::string> ResolveWindowsProtocolV1Root(
    absl::string_view override_root,
    absl::string_view app_data_directory) {
  if (!override_root.empty()) {
    if (absl::StatusOr<ParsedRootPath> parsed = ParseRootPath(override_root);
        !parsed.ok()) {
      return parsed.status();
    }
    return std::string(override_root);
  }
  absl::StatusOr<std::wstring> app_data =
      Utf8ToWideStrict(app_data_directory, "AppData directory");
  if (!app_data.ok()) {
    return app_data.status();
  }
  while (!app_data->empty() &&
         (app_data->back() == L'\\' || app_data->back() == L'/')) {
    app_data->pop_back();
  }
  app_data->append(L"\\");
  app_data->append(kProtocolV1RelativeRoot);
  absl::StatusOr<std::string> resolved =
      WideToUtf8Strict("resolved Grimodex root", *app_data);
  if (!resolved.ok()) {
    return resolved.status();
  }
  if (absl::StatusOr<ParsedRootPath> parsed = ParseRootPath(*resolved);
      !parsed.ok()) {
    return parsed.status();
  }
  return resolved;
}

absl::StatusOr<std::string> ResolveWindowsProtocolV1Root(
    absl::string_view override_root) {
  if (!override_root.empty()) {
    return ResolveWindowsProtocolV1Root(override_root, "unused");
  }
  absl::StatusOr<std::wstring> app_data = ReadRoamingAppDataKnownFolder();
  if (!app_data.ok()) {
    return app_data.status();
  }
  absl::StatusOr<std::string> app_data_utf8 =
      WideToUtf8Strict("FOLDERID_RoamingAppData", *app_data);
  if (!app_data_utf8.ok()) {
    return app_data_utf8.status();
  }
  return ResolveWindowsProtocolV1Root("", *app_data_utf8);
}

}  // namespace mozc::grimodex

#endif  // defined(_WIN32)
