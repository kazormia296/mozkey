// Copyright 2026 The Mozkey Authors

#include "grimodex/protocol_v1_windows_secure_reader.h"

#if defined(_WIN32)

#include <aclapi.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/string_view.h"
#include "base/file/temp_dir.h"
#include "base/win32/wide_char.h"
#include "grimodex/protocol_v1.h"
#include "testing/gunit.h"
#include "testing/mozctest.h"

namespace mozc::grimodex {
namespace {

constexpr char kState[] = R"json({
  "format_version": 1,
  "active_project_id": "project-a",
  "updated_at": "2026-07-11T00:00:00.000Z"
})json";

constexpr char kProject[] = R"json({
  "format_version": 1,
  "project_id": "project-a",
  "project_name": "Project A",
  "generated_at": "2026-07-11T00:00:00.000Z",
  "entries": []
})json";

class ScopedHandle final {
 public:
  explicit ScopedHandle(HANDLE handle = INVALID_HANDLE_VALUE)
      : handle_(handle) {}
  ~ScopedHandle() {
    if (handle_ != nullptr && handle_ != INVALID_HANDLE_VALUE) {
      ::CloseHandle(handle_);
    }
  }
  ScopedHandle(const ScopedHandle &) = delete;
  ScopedHandle &operator=(const ScopedHandle &) = delete;

  HANDLE get() const { return handle_; }

 private:
  HANDLE handle_;
};

class ScopedLocalMemory final {
 public:
  explicit ScopedLocalMemory(HLOCAL memory = nullptr) : memory_(memory) {}
  ~ScopedLocalMemory() {
    if (memory_ != nullptr) {
      ::LocalFree(memory_);
    }
  }
  ScopedLocalMemory(const ScopedLocalMemory &) = delete;
  ScopedLocalMemory &operator=(const ScopedLocalMemory &) = delete;

 private:
  HLOCAL memory_;
};

class PrivateSecurityAttributes final {
 public:
  explicit PrivateSecurityAttributes(bool directory)
      : valid_(Initialize(directory)) {}
  ~PrivateSecurityAttributes() {
    if (acl_ != nullptr) {
      ::LocalFree(acl_);
    }
  }
  PrivateSecurityAttributes(const PrivateSecurityAttributes &) = delete;
  PrivateSecurityAttributes &operator=(const PrivateSecurityAttributes &) =
      delete;

  bool valid() const { return valid_; }
  SECURITY_ATTRIBUTES *get() { return valid_ ? &attributes_ : nullptr; }

 private:
  bool Initialize(bool directory) {
    HANDLE raw_token = nullptr;
    if (!::OpenProcessToken(::GetCurrentProcess(), TOKEN_QUERY, &raw_token)) {
      ADD_FAILURE() << "OpenProcessToken failed: " << ::GetLastError();
      return false;
    }
    ScopedHandle token(raw_token);
    DWORD token_size = 0;
    ::GetTokenInformation(token.get(), TokenUser, nullptr, 0, &token_size);
    if (token_size == 0) {
      ADD_FAILURE() << "GetTokenInformation size query failed: "
                    << ::GetLastError();
      return false;
    }
    token_buffer_.resize(token_size);
    if (!::GetTokenInformation(token.get(), TokenUser, token_buffer_.data(),
                               token_size, &token_size)) {
      ADD_FAILURE() << "GetTokenInformation failed: " << ::GetLastError();
      return false;
    }
    PSID current_user =
        reinterpret_cast<TOKEN_USER *>(token_buffer_.data())->User.Sid;

    DWORD system_size = sizeof(system_sid_);
    if (!::CreateWellKnownSid(WinLocalSystemSid, nullptr, system_sid_.data(),
                              &system_size)) {
      ADD_FAILURE() << "CreateWellKnownSid(LocalSystem) failed: "
                    << ::GetLastError();
      return false;
    }
    DWORD administrators_size = sizeof(administrators_sid_);
    if (!::CreateWellKnownSid(WinBuiltinAdministratorsSid, nullptr,
                              administrators_sid_.data(),
                              &administrators_size)) {
      ADD_FAILURE() << "CreateWellKnownSid(Administrators) failed: "
                    << ::GetLastError();
      return false;
    }

    std::array<EXPLICIT_ACCESSW, 3> entries = {};
    const DWORD inheritance =
        directory ? SUB_CONTAINERS_AND_OBJECTS_INHERIT : NO_INHERITANCE;
    const std::array<PSID, 3> trustees = {
        current_user, system_sid_.data(), administrators_sid_.data()};
    for (size_t index = 0; index < entries.size(); ++index) {
      entries[index].grfAccessPermissions = FILE_ALL_ACCESS;
      entries[index].grfAccessMode = SET_ACCESS;
      entries[index].grfInheritance = inheritance;
      ::BuildTrusteeWithSidW(&entries[index].Trustee, trustees[index]);
    }
    const DWORD acl_error = ::SetEntriesInAclW(
        static_cast<ULONG>(entries.size()), entries.data(), nullptr, &acl_);
    if (acl_error != ERROR_SUCCESS) {
      ADD_FAILURE() << "SetEntriesInAclW failed: " << acl_error;
      return false;
    }
    if (!::InitializeSecurityDescriptor(&descriptor_,
                                        SECURITY_DESCRIPTOR_REVISION) ||
        !::SetSecurityDescriptorOwner(&descriptor_, current_user, FALSE) ||
        !::SetSecurityDescriptorDacl(&descriptor_, TRUE, acl_, FALSE) ||
        !::SetSecurityDescriptorControl(&descriptor_, SE_DACL_PROTECTED,
                                        SE_DACL_PROTECTED)) {
      ADD_FAILURE() << "Security descriptor initialization failed: "
                    << ::GetLastError();
      return false;
    }
    attributes_.nLength = sizeof(attributes_);
    attributes_.lpSecurityDescriptor = &descriptor_;
    attributes_.bInheritHandle = FALSE;
    return true;
  }

  std::vector<uint8_t> token_buffer_;
  std::array<DWORD, SECURITY_MAX_SID_SIZE / sizeof(DWORD)> system_sid_ = {};
  std::array<DWORD, SECURITY_MAX_SID_SIZE / sizeof(DWORD)>
      administrators_sid_ = {};
  PACL acl_ = nullptr;
  SECURITY_DESCRIPTOR descriptor_ = {};
  SECURITY_ATTRIBUTES attributes_ = {};
  bool valid_ = false;
};

void WriteAll(const std::wstring &path, absl::string_view bytes) {
  PrivateSecurityAttributes security(/*directory=*/false);
  ASSERT_TRUE(security.valid());
  ScopedHandle file(::CreateFileW(
      path.c_str(), GENERIC_WRITE,
      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, security.get(),
      CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr));
  ASSERT_NE(file.get(), INVALID_HANDLE_VALUE) << ::GetLastError();
  size_t offset = 0;
  while (offset < bytes.size()) {
    const size_t remaining = bytes.size() - offset;
    const DWORD request = remaining > MAXDWORD
                              ? MAXDWORD
                              : static_cast<DWORD>(remaining);
    DWORD written = 0;
    ASSERT_TRUE(::WriteFile(file.get(), bytes.data() + offset, request,
                            &written, nullptr))
        << ::GetLastError();
    ASSERT_GT(written, 0u);
    offset += written;
  }
}

class Sandbox final {
 public:
  Sandbox()
      : temp_(testing::MakeTempDirectoryOrDie()),
        root_(win32::Utf8ToWide(temp_.path()) + L"\\ime"),
        projects_(root_ + L"\\projects") {
    PrivateSecurityAttributes root_security(/*directory=*/true);
    if (!root_security.valid()) {
      return;
    }
    EXPECT_TRUE(::CreateDirectoryW(root_.c_str(), root_security.get()))
        << ::GetLastError();
    PrivateSecurityAttributes projects_security(/*directory=*/true);
    if (!projects_security.valid()) {
      return;
    }
    EXPECT_TRUE(::CreateDirectoryW(projects_.c_str(), projects_security.get()))
        << ::GetLastError();
  }

  std::string root_utf8() const { return win32::WideToUtf8(root_); }
  const std::wstring &root() const { return root_; }
  const std::wstring &projects() const { return projects_; }
  std::wstring state_path() const { return root_ + L"\\state.json"; }
  std::wstring project_path() const {
    return projects_ + L"\\project-a.json";
  }

  void InstallState(absl::string_view bytes) const {
    WriteAll(state_path(), bytes);
  }
  void InstallProject(absl::string_view bytes) const {
    WriteAll(project_path(), bytes);
  }

  WindowsSecureProtocolV1FileReader Reader() const {
    return WindowsSecureProtocolV1FileReader(root_utf8());
  }

 private:
  TempDirectory temp_;
  std::wstring root_;
  std::wstring projects_;
};

TEST(ProtocolV1WindowsSecureReaderTest,
     ResolvesUtf8OverrideThenAppDataFallback) {
  absl::StatusOr<std::string> override = ResolveWindowsProtocolV1Root(
      R"(C:\Users\tester\辞書)", R"(C:\ignored)");
  ASSERT_TRUE(override.ok()) << override.status();
  EXPECT_EQ(*override, R"(C:\Users\tester\辞書)");

  absl::StatusOr<std::string> fallback = ResolveWindowsProtocolV1Root(
      "", R"(C:\Users\tester\AppData\Roaming\)");
  ASSERT_TRUE(fallback.ok()) << fallback.status();
  EXPECT_EQ(*fallback,
            R"(C:\Users\tester\AppData\Roaming\com.miyakey.grimodex\ime)");
}

TEST(ProtocolV1WindowsSecureReaderTest, RejectsInvalidOrRelativeRootPaths) {
  EXPECT_EQ(ResolveWindowsProtocolV1Root(R"(..\ime)", R"(C:\ignored)")
                .status()
                .code(),
            absl::StatusCode::kInvalidArgument);
  EXPECT_EQ(ResolveWindowsProtocolV1Root(R"(C:\safe\..\ime)",
                                         R"(C:\ignored)")
                .status()
                .code(),
            absl::StatusCode::kInvalidArgument);
  EXPECT_EQ(ResolveWindowsProtocolV1Root(std::string("\xff", 1),
                                         R"(C:\ignored)")
                .status()
                .code(),
            absl::StatusCode::kInvalidArgument);
}

TEST(ProtocolV1WindowsSecureReaderTest, ReadsValidStateAndProject) {
  Sandbox sandbox;
  sandbox.InstallState(kState);
  sandbox.InstallProject(kProject);
  WindowsSecureProtocolV1FileReader reader = sandbox.Reader();

  absl::StatusOr<VerifiedFileBytes> state =
      reader.ReadState(ProtocolV1Limits::kStateBytes);
  ASSERT_TRUE(state.ok()) << state.status();
  EXPECT_EQ(state->bytes, kState);
  EXPECT_EQ(state->sha256, VerifiedFileBytes::FromBytes(kState).sha256);

  absl::StatusOr<VerifiedFileBytes> project =
      reader.ReadProject("project-a", ProtocolV1Limits::kProjectBytes);
  ASSERT_TRUE(project.ok()) << project.status();
  EXPECT_EQ(project->bytes, kProject);
  EXPECT_EQ(project->sha256, VerifiedFileBytes::FromBytes(kProject).sha256);
}

TEST(ProtocolV1WindowsSecureReaderTest, RejectsOversizeBeforeReading) {
  Sandbox sandbox;
  sandbox.InstallState("12345");
  absl::StatusOr<VerifiedFileBytes> result = sandbox.Reader().ReadState(4);
  ASSERT_FALSE(result.ok());
  EXPECT_EQ(result.status().code(), absl::StatusCode::kResourceExhausted);
}

TEST(ProtocolV1WindowsSecureReaderTest, RejectsUnsafeProjectIdentifiers) {
  Sandbox sandbox;
  absl::StatusOr<VerifiedFileBytes> result =
      sandbox.Reader().ReadProject("../outside", 100);
  ASSERT_FALSE(result.ok());
  EXPECT_EQ(result.status().code(), absl::StatusCode::kInvalidArgument);
}

TEST(ProtocolV1WindowsSecureReaderTest, RejectsDirectoryInPlaceOfFile) {
  Sandbox sandbox;
  ASSERT_TRUE(::CreateDirectoryW(sandbox.state_path().c_str(), nullptr))
      << ::GetLastError();
  absl::StatusOr<VerifiedFileBytes> result = sandbox.Reader().ReadState(100);
  ASSERT_FALSE(result.ok());
  EXPECT_EQ(result.status().code(), absl::StatusCode::kFailedPrecondition);
}

TEST(ProtocolV1WindowsSecureReaderTest, RejectsSymbolicLinkWhenSupported) {
  Sandbox sandbox;
  const std::wstring target = sandbox.root() + L"\\target.json";
  WriteAll(target, kState);
  constexpr DWORD kAllowUnprivilegedCreate = 0x2;
  if (!::CreateSymbolicLinkW(sandbox.state_path().c_str(), L"target.json",
                             kAllowUnprivilegedCreate)) {
    const DWORD error = ::GetLastError();
    if (error == ERROR_PRIVILEGE_NOT_HELD || error == ERROR_NOT_SUPPORTED ||
        error == ERROR_INVALID_PARAMETER) {
      GTEST_SKIP() << "symbolic-link creation is unavailable: " << error;
    }
    FAIL() << "CreateSymbolicLinkW failed: " << error;
  }
  absl::StatusOr<VerifiedFileBytes> result = sandbox.Reader().ReadState(1000);
  ASSERT_FALSE(result.ok());
  EXPECT_EQ(result.status().code(), absl::StatusCode::kPermissionDenied);
}

TEST(ProtocolV1WindowsSecureReaderTest,
     FileVersionComparisonCoversEveryMutationSignal) {
  protocol_v1_windows_internal::FileVersion baseline;
  baseline.volume_serial_number = 1;
  baseline.file_id[0] = 2;
  baseline.size = 3;
  baseline.last_write_time = 4;
  baseline.change_time = 5;
  baseline.number_of_links = 1;
  EXPECT_TRUE(
      protocol_v1_windows_internal::SameFileVersion(baseline, baseline));

  auto changed = baseline;
  ++changed.volume_serial_number;
  EXPECT_FALSE(
      protocol_v1_windows_internal::SameFileVersion(baseline, changed));
  changed = baseline;
  ++changed.file_id[0];
  EXPECT_FALSE(
      protocol_v1_windows_internal::SameFileVersion(baseline, changed));
  changed = baseline;
  ++changed.size;
  EXPECT_FALSE(
      protocol_v1_windows_internal::SameFileVersion(baseline, changed));
  changed = baseline;
  ++changed.last_write_time;
  EXPECT_FALSE(
      protocol_v1_windows_internal::SameFileVersion(baseline, changed));
  changed = baseline;
  ++changed.change_time;
  EXPECT_FALSE(
      protocol_v1_windows_internal::SameFileVersion(baseline, changed));
  changed = baseline;
  ++changed.number_of_links;
  EXPECT_FALSE(
      protocol_v1_windows_internal::SameFileVersion(baseline, changed));
}

TEST(ProtocolV1WindowsSecureReaderTest,
     SecurityMaskDistinguishesReadFromMutationRights) {
  EXPECT_FALSE(protocol_v1_windows_internal::AccessMaskAllowsMutation(
      FILE_GENERIC_READ | FILE_GENERIC_EXECUTE));
  EXPECT_TRUE(protocol_v1_windows_internal::AccessMaskAllowsMutation(
      FILE_WRITE_DATA));
  EXPECT_TRUE(protocol_v1_windows_internal::AccessMaskAllowsMutation(DELETE));
  EXPECT_TRUE(
      protocol_v1_windows_internal::AccessMaskAllowsMutation(WRITE_DAC));
}

TEST(ProtocolV1WindowsSecureReaderTest,
     AcceptsCurrentUserSecurityAndRejectsUntrustedWriter) {
  Sandbox sandbox;
  sandbox.InstallState(kState);
  std::wstring path = sandbox.state_path();
  ScopedHandle file(::CreateFileW(
      path.c_str(), READ_CONTROL,
      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
      OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
  ASSERT_NE(file.get(), INVALID_HANDLE_VALUE) << ::GetLastError();
  EXPECT_TRUE(protocol_v1_windows_internal::ValidateCurrentUserSecurity(
                  file.get(), "state.json")
                  .ok());

  PACL old_dacl = nullptr;
  PSECURITY_DESCRIPTOR descriptor = nullptr;
  ASSERT_EQ(::GetNamedSecurityInfoW(
                path.data(), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
                nullptr, nullptr, &old_dacl, nullptr, &descriptor),
            ERROR_SUCCESS);
  ScopedLocalMemory descriptor_owner(descriptor);

  std::array<DWORD, SECURITY_MAX_SID_SIZE / sizeof(DWORD)> world_storage = {};
  DWORD world_size = sizeof(world_storage);
  ASSERT_TRUE(::CreateWellKnownSid(WinWorldSid, nullptr, world_storage.data(),
                                   &world_size))
      << ::GetLastError();
  EXPLICIT_ACCESSW access = {};
  access.grfAccessPermissions = FILE_WRITE_DATA | DELETE;
  access.grfAccessMode = GRANT_ACCESS;
  access.grfInheritance = NO_INHERITANCE;
  ::BuildTrusteeWithSidW(&access.Trustee, world_storage.data());
  PACL contaminated_dacl = nullptr;
  ASSERT_EQ(::SetEntriesInAclW(1, &access, old_dacl, &contaminated_dacl),
            ERROR_SUCCESS);
  ScopedLocalMemory contaminated_owner(contaminated_dacl);
  ASSERT_EQ(::SetNamedSecurityInfoW(
                path.data(), SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                nullptr, nullptr, contaminated_dacl, nullptr),
            ERROR_SUCCESS);

  const absl::Status security =
      protocol_v1_windows_internal::ValidateCurrentUserSecurity(file.get(),
                                                                 "state.json");
  EXPECT_EQ(security.code(), absl::StatusCode::kPermissionDenied);
  absl::StatusOr<VerifiedFileBytes> result = sandbox.Reader().ReadState(1000);
  ASSERT_FALSE(result.ok());
  EXPECT_EQ(result.status().code(), absl::StatusCode::kPermissionDenied);
}

}  // namespace
}  // namespace mozc::grimodex

#endif  // defined(_WIN32)
