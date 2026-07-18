// Copyright 2026 The Mozkey Authors

#include "grimodex/consumer_file_registrar_windows.h"

#if defined(_WIN32)

#include <windows.h>

#include <aclapi.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/string_view.h"
#include "grimodex/consumer_handshake.h"
#include "grimodex/protocol_v1_windows_secure_reader.h"

namespace mozc::grimodex {
namespace {

constexpr int kCreateAttempts = 32;
// FileRenameInfoEx is value 22 in FILE_INFO_BY_HANDLE_CLASS.  Some Windows SDK
// configurations hide its named enumerator when NTDDI_VERSION is older than
// Windows 10 RS1 even though the project targets Windows 10 and the API is
// available on the supported Windows 10 RS1-and-later runtimes.
constexpr FILE_INFO_BY_HANDLE_CLASS kFileRenameInfoEx =
    static_cast<FILE_INFO_BY_HANDLE_CLASS>(22);

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
  void reset(HLOCAL memory) {
    if (memory_ != nullptr) {
      ::LocalFree(memory_);
    }
    memory_ = memory;
  }

 private:
  HLOCAL memory_;
};

absl::Status WindowsError(absl::string_view operation, DWORD error) {
  const std::string message =
      absl::StrCat(operation, " failed (win32_error=", error, ")");
  switch (error) {
    case ERROR_FILE_NOT_FOUND:
    case ERROR_PATH_NOT_FOUND:
      return absl::NotFoundError(message);
    case ERROR_ACCESS_DENIED:
    case ERROR_SHARING_VIOLATION:
    case ERROR_LOCK_VIOLATION:
    case ERROR_PRIVILEGE_NOT_HELD:
      return absl::PermissionDeniedError(message);
    case ERROR_INVALID_NAME:
    case ERROR_BAD_PATHNAME:
    case ERROR_FILENAME_EXCED_RANGE:
    case ERROR_NO_UNICODE_TRANSLATION:
      return absl::InvalidArgumentError(message);
    default:
      return absl::FailedPreconditionError(message);
  }
}

absl::StatusOr<std::wstring> Utf8ToWideStrict(absl::string_view input,
                                               absl::string_view label) {
  if (input.empty() || input.find('\0') != absl::string_view::npos ||
      input.size() > static_cast<size_t>((std::numeric_limits<int>::max)())) {
    return absl::InvalidArgumentError(
        absl::StrCat(label, " is empty, too long, or contains NUL"));
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

bool IsSafeConsumerId(absl::string_view id) {
  if (id.empty() || id.size() > 64) {
    return false;
  }
  const auto is_alphanumeric = [](unsigned char byte) {
    return (byte >= 'a' && byte <= 'z') ||
           (byte >= '0' && byte <= '9');
  };
  if (!is_alphanumeric(id.front()) || !is_alphanumeric(id.back())) {
    return false;
  }
  for (const unsigned char byte : id) {
    if (!(is_alphanumeric(byte) || byte == '-' || byte == '_' ||
          byte == '.')) {
      return false;
    }
  }
  return true;
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
  return component.size() == 4 &&
         (component.compare(0, 3, L"COM") == 0 ||
          component.compare(0, 3, L"LPT") == 0) &&
         component[3] >= L'1' && component[3] <= L'9';
}

absl::Status ValidatePathComponent(std::wstring_view component) {
  if (component.empty() || component == L"." || component == L".." ||
      component.back() == L'.' || component.back() == L' ') {
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

struct ParsedRootPath final {
  std::vector<std::wstring> prefixes;
};

absl::StatusOr<ParsedRootPath> ParseRootPath(absl::string_view utf8_path) {
  absl::StatusOr<std::wstring> converted =
      Utf8ToWideStrict(utf8_path, "Grimodex root");
  if (!converted.ok()) {
    return converted.status();
  }
  std::wstring path = std::move(*converted);
  std::replace(path.begin(), path.end(), L'/', L'\\');
  if (path.back() == L'\\' || path.rfind(L"\\\\?\\", 0) == 0 ||
      path.rfind(L"\\\\.\\", 0) == 0) {
    return absl::InvalidArgumentError(
        "Grimodex root is not a canonical absolute path");
  }

  ParsedRootPath result;
  std::wstring prefix;
  size_t component_begin = 0;
  if (path.size() >= 3 && IsAsciiAlpha(path[0]) && path[1] == L':' &&
      path[2] == L'\\') {
    prefix = L"\\\\?\\" + path.substr(0, 3);
    result.prefixes.push_back(prefix);
    component_begin = 3;
  } else if (path.rfind(L"\\\\", 0) == 0) {
    const size_t server_end = path.find(L'\\', 2);
    const size_t share_end =
        server_end == std::wstring::npos
            ? std::wstring::npos
            : path.find(L'\\', server_end + 1);
    if (server_end == std::wstring::npos || share_end == std::wstring::npos) {
      return absl::InvalidArgumentError(
          "UNC Grimodex root must include a share and child directory");
    }
    const std::wstring server = path.substr(2, server_end - 2);
    const std::wstring share =
        path.substr(server_end + 1, share_end - server_end - 1);
    if (absl::Status status = ValidatePathComponent(server); !status.ok()) {
      return status;
    }
    if (absl::Status status = ValidatePathComponent(share); !status.ok()) {
      return status;
    }
    prefix = L"\\\\?\\UNC\\" + server + L"\\" + share;
    result.prefixes.push_back(prefix);
    component_begin = share_end + 1;
  } else {
    return absl::InvalidArgumentError(
        "Grimodex root must be an absolute drive or UNC path");
  }

  size_t component_count = 0;
  while (component_begin < path.size()) {
    const size_t end = path.find(L'\\', component_begin);
    const std::wstring component = path.substr(
        component_begin, end == std::wstring::npos
                             ? path.size() - component_begin
                             : end - component_begin);
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
        "filesystem volume or share cannot be a Grimodex root");
  }
  return result;
}

class PrivateSecurity final {
 public:
  ~PrivateSecurity() = default;
  PrivateSecurity(const PrivateSecurity &) = delete;
  PrivateSecurity &operator=(const PrivateSecurity &) = delete;

  static absl::StatusOr<std::unique_ptr<PrivateSecurity>> Create(
      bool directory) {
    std::unique_ptr<PrivateSecurity> security(new PrivateSecurity);
    HANDLE raw_token = nullptr;
    if (!::OpenProcessToken(::GetCurrentProcess(), TOKEN_QUERY, &raw_token)) {
      return WindowsError("open current process token", ::GetLastError());
    }
    UniqueHandle token(raw_token);
    DWORD token_size = 0;
    ::GetTokenInformation(token.get(), TokenUser, nullptr, 0, &token_size);
    if (token_size == 0) {
      return WindowsError("query current user SID size", ::GetLastError());
    }
    security->token_buffer_.resize(token_size);
    if (!::GetTokenInformation(token.get(), TokenUser,
                               security->token_buffer_.data(), token_size,
                               &token_size)) {
      return WindowsError("query current user SID", ::GetLastError());
    }
    PSID current_user = reinterpret_cast<TOKEN_USER *>(
                            security->token_buffer_.data())
                            ->User.Sid;
    if (!::IsValidSid(current_user)) {
      return absl::FailedPreconditionError("current process has invalid SID");
    }

    DWORD system_size = sizeof(security->system_sid_);
    if (!::CreateWellKnownSid(WinLocalSystemSid, nullptr,
                              security->system_sid_.data(), &system_size)) {
      return WindowsError("create LocalSystem SID", ::GetLastError());
    }
    DWORD administrators_size = sizeof(security->administrators_sid_);
    if (!::CreateWellKnownSid(WinBuiltinAdministratorsSid, nullptr,
                              security->administrators_sid_.data(),
                              &administrators_size)) {
      return WindowsError("create Administrators SID", ::GetLastError());
    }

    std::array<EXPLICIT_ACCESSW, 3> entries = {};
    const DWORD inheritance =
        directory ? SUB_CONTAINERS_AND_OBJECTS_INHERIT : NO_INHERITANCE;
    const std::array<PSID, 3> trustees = {
        current_user, security->system_sid_.data(),
        security->administrators_sid_.data()};
    for (size_t index = 0; index < entries.size(); ++index) {
      entries[index].grfAccessPermissions = FILE_ALL_ACCESS;
      entries[index].grfAccessMode = SET_ACCESS;
      entries[index].grfInheritance = inheritance;
      ::BuildTrusteeWithSidW(&entries[index].Trustee, trustees[index]);
    }
    PACL acl = nullptr;
    const DWORD acl_error = ::SetEntriesInAclW(
        static_cast<ULONG>(entries.size()), entries.data(), nullptr, &acl);
    if (acl_error != ERROR_SUCCESS) {
      return WindowsError("create private Grimodex DACL", acl_error);
    }
    security->acl_.reset(acl);
    if (!::InitializeSecurityDescriptor(&security->descriptor_,
                                        SECURITY_DESCRIPTOR_REVISION) ||
        !::SetSecurityDescriptorOwner(&security->descriptor_, current_user,
                                      FALSE) ||
        !::SetSecurityDescriptorDacl(&security->descriptor_, TRUE, acl,
                                     FALSE) ||
        !::SetSecurityDescriptorControl(&security->descriptor_,
                                        SE_DACL_PROTECTED,
                                        SE_DACL_PROTECTED)) {
      return WindowsError("initialize private Grimodex security descriptor",
                          ::GetLastError());
    }
    security->attributes_.nLength = sizeof(SECURITY_ATTRIBUTES);
    security->attributes_.lpSecurityDescriptor = &security->descriptor_;
    security->attributes_.bInheritHandle = FALSE;
    return security;
  }

  SECURITY_ATTRIBUTES *attributes() { return &attributes_; }

 private:
  PrivateSecurity() = default;

  std::vector<uint8_t> token_buffer_;
  std::array<DWORD, SECURITY_MAX_SID_SIZE / sizeof(DWORD)> system_sid_ = {};
  std::array<DWORD, SECURITY_MAX_SID_SIZE / sizeof(DWORD)>
      administrators_sid_ = {};
  UniqueLocalMemory acl_;
  SECURITY_DESCRIPTOR descriptor_ = {};
  SECURITY_ATTRIBUTES attributes_ = {};
};

absl::StatusOr<UniqueHandle> OpenPath(const std::wstring &path, DWORD access,
                                      bool directory,
                                      absl::string_view label) {
  DWORD flags = FILE_FLAG_OPEN_REPARSE_POINT;
  if (directory) {
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

absl::Status ValidateDirectory(HANDLE handle, absl::string_view label,
                               bool validate_security) {
  FILE_BASIC_INFO basic = {};
  if (!::GetFileInformationByHandleEx(handle, FileBasicInfo, &basic,
                                      sizeof(basic))) {
    return WindowsError(absl::StrCat("inspect ", label), ::GetLastError());
  }
  if ((basic.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " is a reparse point"));
  }
  FILE_STANDARD_INFO standard = {};
  if (!::GetFileInformationByHandleEx(handle, FileStandardInfo, &standard,
                                      sizeof(standard))) {
    return WindowsError(absl::StrCat("inspect ", label, " type"),
                        ::GetLastError());
  }
  if (!standard.Directory) {
    return absl::FailedPreconditionError(
        absl::StrCat(label, " is not a directory"));
  }
  return validate_security
             ? protocol_v1_windows_internal::ValidateCurrentUserSecurity(
                   handle, label)
             : absl::OkStatus();
}

struct SecureDirectory final {
  UniqueHandle handle;
  std::wstring path;
};

absl::StatusOr<SecureDirectory> OpenPrivateRoot(absl::string_view root,
                                                bool create) {
  absl::StatusOr<ParsedRootPath> parsed = ParseRootPath(root);
  if (!parsed.ok()) {
    return parsed.status();
  }
  absl::StatusOr<std::unique_ptr<PrivateSecurity>> private_directory =
      PrivateSecurity::Create(/*directory=*/true);
  if (!private_directory.ok()) {
    return private_directory.status();
  }
  std::vector<UniqueHandle> chain;
  chain.reserve(parsed->prefixes.size());
  for (size_t index = 0; index < parsed->prefixes.size(); ++index) {
    const bool is_volume_or_share = index == 0;
    const bool is_final = index + 1 == parsed->prefixes.size();
    if (create && !is_volume_or_share &&
        !::CreateDirectoryW(parsed->prefixes[index].c_str(),
                            (*private_directory)->attributes())) {
      const DWORD error = ::GetLastError();
      if (error != ERROR_ALREADY_EXISTS) {
        return WindowsError("create Grimodex consumer directory tree", error);
      }
    }
    const DWORD access = FILE_READ_ATTRIBUTES | (is_final ? READ_CONTROL : 0);
    absl::StatusOr<UniqueHandle> handle = OpenPath(
        parsed->prefixes[index], access, /*directory=*/true,
        is_final ? "Grimodex consumer root"
                 : "Grimodex consumer root ancestor");
    if (!handle.ok()) {
      return handle.status();
    }
    if (absl::Status status = ValidateDirectory(
            handle->get(), is_final ? "Grimodex consumer root"
                                    : "Grimodex consumer root ancestor",
            /*validate_security=*/is_final);
        !status.ok()) {
      return status;
    }
    chain.push_back(std::move(*handle));
  }
  UniqueHandle final = std::move(chain.back());
  return SecureDirectory{.handle = std::move(final),
                         .path = parsed->prefixes.back()};
}

absl::StatusOr<SecureDirectory> OpenConsumersDirectory(
    const SecureDirectory &root, bool create) {
  const std::wstring path = root.path + L"\\consumers";
  if (create) {
    absl::StatusOr<std::unique_ptr<PrivateSecurity>> private_directory =
        PrivateSecurity::Create(/*directory=*/true);
    if (!private_directory.ok()) {
      return private_directory.status();
    }
    if (!::CreateDirectoryW(path.c_str(),
                            (*private_directory)->attributes())) {
      const DWORD error = ::GetLastError();
      if (error != ERROR_ALREADY_EXISTS) {
        return WindowsError("create Grimodex consumers directory", error);
      }
    }
  }
  absl::StatusOr<UniqueHandle> handle =
      OpenPath(path, FILE_READ_ATTRIBUTES | READ_CONTROL,
               /*directory=*/true, "Grimodex consumers directory");
  if (!handle.ok()) {
    return handle.status();
  }
  if (absl::Status status = ValidateDirectory(
          handle->get(), "Grimodex consumers directory",
          /*validate_security=*/true);
      !status.ok()) {
    return status;
  }
  return SecureDirectory{.handle = std::move(*handle), .path = path};
}

absl::Status InspectConsumerEntry(const std::wstring &path,
                                  bool allow_missing) {
  absl::StatusOr<UniqueHandle> file =
      OpenPath(path, FILE_READ_ATTRIBUTES | READ_CONTROL,
               /*directory=*/false, "Grimodex consumer handshake");
  if (allow_missing && absl::IsNotFound(file.status())) {
    return absl::OkStatus();
  }
  if (!file.ok()) {
    return file.status();
  }
  if (::GetFileType(file->get()) != FILE_TYPE_DISK) {
    return absl::FailedPreconditionError(
        "Grimodex consumer handshake is not a disk file");
  }
  FILE_BASIC_INFO basic = {};
  if (!::GetFileInformationByHandleEx(file->get(), FileBasicInfo, &basic,
                                      sizeof(basic))) {
    return WindowsError("inspect Grimodex consumer handshake attributes",
                        ::GetLastError());
  }
  if ((basic.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
    return absl::PermissionDeniedError(
        "Grimodex consumer handshake is a reparse point");
  }
  FILE_STANDARD_INFO standard = {};
  if (!::GetFileInformationByHandleEx(file->get(), FileStandardInfo,
                                      &standard, sizeof(standard))) {
    return WindowsError("inspect Grimodex consumer handshake metadata",
                        ::GetLastError());
  }
  if (standard.Directory) {
    return absl::FailedPreconditionError(
        "Grimodex consumer handshake is a directory");
  }
  if (standard.NumberOfLinks != 1) {
    return absl::PermissionDeniedError(
        "Grimodex consumer handshake must have exactly one hard link");
  }
  return protocol_v1_windows_internal::ValidateCurrentUserSecurity(
      file->get(), "Grimodex consumer handshake");
}

absl::Status WriteAll(HANDLE file, absl::string_view bytes) {
  size_t offset = 0;
  while (offset < bytes.size()) {
    const size_t remaining = bytes.size() - offset;
    const DWORD request = remaining > (std::numeric_limits<DWORD>::max)()
                              ? (std::numeric_limits<DWORD>::max)()
                              : static_cast<DWORD>(remaining);
    DWORD written = 0;
    if (!::WriteFile(file, bytes.data() + offset, request, &written,
                     nullptr)) {
      return WindowsError("write Grimodex consumer handshake",
                          ::GetLastError());
    }
    if (written == 0) {
      return absl::FailedPreconditionError(
          "write Grimodex consumer handshake made no progress");
    }
    offset += written;
  }
  return absl::OkStatus();
}

absl::Status AtomicReplace(const SecureDirectory &consumers,
                           absl::string_view consumer_id,
                           absl::string_view bytes) {
  absl::StatusOr<std::wstring> id =
      Utf8ToWideStrict(consumer_id, "Grimodex consumer ID");
  if (!id.ok()) {
    return id.status();
  }
  const std::wstring destination = consumers.path + L"\\" + *id + L".json";
  if (absl::Status status =
          InspectConsumerEntry(destination, /*allow_missing=*/true);
      !status.ok()) {
    return status;
  }
  absl::StatusOr<std::unique_ptr<PrivateSecurity>> private_file =
      PrivateSecurity::Create(/*directory=*/false);
  if (!private_file.ok()) {
    return private_file.status();
  }

  static std::atomic<uint64_t> nonce = 0;
  std::wstring temporary_path;
  UniqueHandle temporary;
  for (int attempt = 0; attempt < kCreateAttempts; ++attempt) {
    temporary_path =
        consumers.path + L"\\." + *id + L"." +
        std::to_wstring(::GetCurrentProcessId()) + L"." +
        std::to_wstring(nonce.fetch_add(1)) + L".tmp";
    HANDLE raw = ::CreateFileW(
        temporary_path.c_str(),
        GENERIC_WRITE | FILE_READ_ATTRIBUTES | READ_CONTROL | DELETE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        (*private_file)->attributes(), CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if (raw != INVALID_HANDLE_VALUE) {
      temporary = UniqueHandle(raw);
      break;
    }
    if (::GetLastError() != ERROR_FILE_EXISTS &&
        ::GetLastError() != ERROR_ALREADY_EXISTS) {
      return WindowsError("create Grimodex consumer temporary file",
                          ::GetLastError());
    }
  }
  if (!temporary.valid()) {
    return absl::ResourceExhaustedError(
        "could not allocate a unique Grimodex consumer temporary file");
  }

  bool moved = false;
  auto cleanup = [&] {
    if (!moved) {
      ::DeleteFileW(temporary_path.c_str());
    }
  };
  if (absl::Status status = WriteAll(temporary.get(), bytes); !status.ok()) {
    cleanup();
    return status;
  }
  if (!::FlushFileBuffers(temporary.get())) {
    const absl::Status status =
        WindowsError("flush Grimodex consumer temporary file",
                     ::GetLastError());
    cleanup();
    return status;
  }
  if (absl::Status status =
          protocol_v1_windows_internal::ValidateCurrentUserSecurity(
              temporary.get(), "Grimodex consumer temporary file");
      !status.ok()) {
    cleanup();
    return status;
  }
  if (destination.size() >
      ((std::numeric_limits<DWORD>::max)() - sizeof(FILE_RENAME_INFO)) /
          sizeof(wchar_t)) {
    temporary = UniqueHandle();
    cleanup();
    return absl::InvalidArgumentError(
        "Grimodex consumer handshake path is too long");
  }
  const size_t destination_bytes = destination.size() * sizeof(wchar_t);
  const size_t rename_info_size =
      sizeof(FILE_RENAME_INFO) + destination_bytes;
  const size_t rename_storage_size =
      (rename_info_size + sizeof(std::max_align_t) - 1) /
      sizeof(std::max_align_t);
  std::vector<std::max_align_t> rename_storage(rename_storage_size);
  auto *rename_info =
      reinterpret_cast<FILE_RENAME_INFO *>(rename_storage.data());
  rename_info->Flags = FILE_RENAME_FLAG_REPLACE_IF_EXISTS |
                       FILE_RENAME_FLAG_POSIX_SEMANTICS;
  rename_info->RootDirectory = nullptr;
  rename_info->FileNameLength = static_cast<DWORD>(destination_bytes);
  std::copy(destination.begin(), destination.end(), rename_info->FileName);
  rename_info->FileName[destination.size()] = L'\0';

  // POSIX rename semantics keep existing shared-delete handles attached to the
  // old file while making all subsequent opens resolve to the private
  // replacement.  Unlike ReplaceFileW, a failed rename has no documented
  // partial state that can remove the canonical heartbeat.
  if (!::SetFileInformationByHandle(
          temporary.get(), kFileRenameInfoEx, rename_info,
          static_cast<DWORD>(rename_info_size))) {
    const absl::Status status = WindowsError(
        "replace Grimodex consumer handshake", ::GetLastError());
    temporary = UniqueHandle();
    cleanup();
    return status;
  }
  moved = true;
  temporary = UniqueHandle();
  return InspectConsumerEntry(destination, /*allow_missing=*/false);
}

}  // namespace

WindowsConsumerFileRegistrar::WindowsConsumerFileRegistrar(std::string root)
    : root_(std::move(root)) {}

absl::Status WindowsConsumerFileRegistrar::Refresh(
    const ConsumerHandshake &handshake) const {
  absl::StatusOr<std::string> payload =
      SerializeConsumerHandshake(handshake);
  if (!payload.ok()) {
    return payload.status();
  }
  absl::StatusOr<SecureDirectory> root =
      OpenPrivateRoot(root_, /*create=*/true);
  if (!root.ok()) {
    return root.status();
  }
  absl::StatusOr<SecureDirectory> consumers =
      OpenConsumersDirectory(*root, /*create=*/true);
  if (!consumers.ok()) {
    return consumers.status();
  }
  return AtomicReplace(*consumers, handshake.consumer_id, *payload);
}

absl::Status WindowsConsumerFileRegistrar::Unregister(
    absl::string_view consumer_id) const {
  if (!IsSafeConsumerId(consumer_id)) {
    return absl::InvalidArgumentError("invalid Grimodex consumer ID");
  }
  absl::StatusOr<SecureDirectory> root =
      OpenPrivateRoot(root_, /*create=*/false);
  if (absl::IsNotFound(root.status())) {
    return absl::OkStatus();
  }
  if (!root.ok()) {
    return root.status();
  }
  absl::StatusOr<SecureDirectory> consumers =
      OpenConsumersDirectory(*root, /*create=*/false);
  if (absl::IsNotFound(consumers.status())) {
    return absl::OkStatus();
  }
  if (!consumers.ok()) {
    return consumers.status();
  }
  absl::StatusOr<std::wstring> id =
      Utf8ToWideStrict(consumer_id, "Grimodex consumer ID");
  if (!id.ok()) {
    return id.status();
  }
  const std::wstring destination =
      consumers->path + L"\\" + *id + L".json";
  const absl::Status inspected =
      InspectConsumerEntry(destination, /*allow_missing=*/true);
  if (!inspected.ok()) {
    return inspected;
  }
  if (!::DeleteFileW(destination.c_str())) {
    const DWORD error = ::GetLastError();
    if (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) {
      return absl::OkStatus();
    }
    return WindowsError("remove Grimodex consumer handshake", error);
  }
  return absl::OkStatus();
}

}  // namespace mozc::grimodex

#endif  // defined(_WIN32)
