// Copyright 2026 The Mozkey Authors

#include "grimodex/consumer_file_registrar_windows.h"
#include "grimodex/desktop_consumer_heartbeat.h"

#if defined(_WIN32)

#include <windows.h>

#include <aclapi.h>

#include <cstddef>
#include <string>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "base/file/temp_dir.h"
#include "base/file_util.h"
#include "base/win32/wide_char.h"
#include "grimodex/consumer_handshake.h"
#include "grimodex/protocol_v1_windows_secure_reader.h"
#include "testing/gunit.h"
#include "testing/mozctest.h"

namespace mozc::grimodex {
namespace {

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

bool HasProtectedDacl(HANDLE handle) {
  PSECURITY_DESCRIPTOR descriptor = nullptr;
  const DWORD error = ::GetSecurityInfo(
      handle, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, nullptr, nullptr,
      nullptr, nullptr, &descriptor);
  EXPECT_EQ(error, ERROR_SUCCESS);
  ScopedLocalMemory descriptor_owner(descriptor);
  if (error != ERROR_SUCCESS) {
    return false;
  }
  SECURITY_DESCRIPTOR_CONTROL control = 0;
  DWORD revision = 0;
  EXPECT_TRUE(
      ::GetSecurityDescriptorControl(descriptor, &control, &revision));
  return (control & SE_DACL_PROTECTED) != 0;
}

ConsumerHandshake Handshake(absl::string_view timestamp =
                                "2026-07-18T01:02:03.456Z") {
  return ConsumerHandshake{
      .consumer_id = std::string(kTsfConsumerId),
      .name = "Mozkey for Grimodex on Windows",
      .version = "0.8.0",
      .platform = "windows",
      .last_seen = std::string(timestamp),
      .capabilities = {.profile = true,
                       .dynamic_dictionary = true,
                       .zenzai_v3_conditions = true,
                       .application_scoping = true},
  };
}

std::string ReadHandle(HANDLE handle) {
  LARGE_INTEGER zero = {};
  EXPECT_TRUE(::SetFilePointerEx(handle, zero, nullptr, FILE_BEGIN));
  std::string result(kMaxConsumerHandshakeBytes, '\0');
  DWORD size = 0;
  EXPECT_TRUE(::ReadFile(handle, result.data(),
                         static_cast<DWORD>(result.size()), &size, nullptr));
  result.resize(size);
  return result;
}

class Sandbox final {
 public:
  Sandbox()
      : temp_(testing::MakeTempDirectoryOrDie()),
        root_(win32::Utf8ToWide(temp_.path()) + L"\\fresh\\ime") {}

  std::string root_utf8() const { return win32::WideToUtf8(root_); }
  const std::wstring &root() const { return root_; }
  std::wstring consumers() const { return root_ + L"\\consumers"; }
  std::wstring destination() const {
    return consumers() + L"\\tsf-mozkey.json";
  }

 private:
  TempDirectory temp_;
  std::wstring root_;
};

TEST(WindowsConsumerFileRegistrarTest, CreatesPrivateCanonicalHeartbeat) {
  Sandbox sandbox;
  WindowsConsumerFileRegistrar registrar(sandbox.root_utf8());
  const ConsumerHandshake handshake = Handshake();
  ASSERT_TRUE(registrar.Refresh(handshake).ok());

  absl::StatusOr<std::string> expected =
      SerializeConsumerHandshake(handshake);
  absl::StatusOr<std::string> actual =
      FileUtil::GetContents(win32::WideToUtf8(sandbox.destination()));
  ASSERT_TRUE(expected.ok()) << expected.status();
  ASSERT_TRUE(actual.ok()) << actual.status();
  EXPECT_EQ(*actual, *expected);

  for (const std::wstring &path : {sandbox.root(), sandbox.consumers(),
                                   sandbox.destination()}) {
    ScopedHandle handle(::CreateFileW(
        path.c_str(), FILE_READ_ATTRIBUTES | READ_CONTROL,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
        OPEN_EXISTING,
        path == sandbox.destination()
            ? FILE_FLAG_OPEN_REPARSE_POINT
            : FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
        nullptr));
    ASSERT_NE(handle.get(), INVALID_HANDLE_VALUE) << ::GetLastError();
    EXPECT_TRUE(protocol_v1_windows_internal::ValidateCurrentUserSecurity(
                    handle.get(), "registrar output")
                    .ok());
    EXPECT_TRUE(HasProtectedDacl(handle.get()));
  }
}

TEST(WindowsConsumerFileRegistrarTest, RefreshAtomicallyReplacesOpenFile) {
  Sandbox sandbox;
  WindowsConsumerFileRegistrar registrar(sandbox.root_utf8());
  const ConsumerHandshake first = Handshake();
  ASSERT_TRUE(registrar.Refresh(first).ok());
  ScopedHandle old(::CreateFileW(
      sandbox.destination().c_str(), GENERIC_READ,
      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
      OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
  ASSERT_NE(old.get(), INVALID_HANDLE_VALUE) << ::GetLastError();

  ConsumerHandshake second = Handshake("2026-07-18T01:17:03.456Z");
  second.version = "0.8.1";
  const absl::Status refresh_status = registrar.Refresh(second);
  ASSERT_TRUE(refresh_status.ok()) << refresh_status;
  absl::StatusOr<std::string> old_payload = SerializeConsumerHandshake(first);
  absl::StatusOr<std::string> new_payload = SerializeConsumerHandshake(second);
  ASSERT_TRUE(old_payload.ok());
  ASSERT_TRUE(new_payload.ok());
  EXPECT_EQ(ReadHandle(old.get()), *old_payload);
  absl::StatusOr<std::string> current =
      FileUtil::GetContents(win32::WideToUtf8(sandbox.destination()));
  ASSERT_TRUE(current.ok()) << current.status();
  EXPECT_EQ(*current, *new_payload);
}

TEST(WindowsConsumerFileRegistrarTest,
     UnregisterIsIdempotentAndPreservesOtherState) {
  Sandbox sandbox;
  WindowsConsumerFileRegistrar registrar(sandbox.root_utf8());
  ASSERT_TRUE(registrar.Refresh(Handshake()).ok());
  const std::wstring state = sandbox.root() + L"\\state.json";
  const std::wstring projects = sandbox.root() + L"\\projects";
  const std::wstring other = sandbox.consumers() + L"\\other-ime.json";
  ASSERT_TRUE(::CreateDirectoryW(projects.c_str(), nullptr));
  ASSERT_TRUE(
      FileUtil::SetContents(win32::WideToUtf8(state), "state\n").ok());
  ASSERT_TRUE(
      FileUtil::SetContents(win32::WideToUtf8(other), "other\n").ok());

  EXPECT_TRUE(registrar.Unregister(kTsfConsumerId).ok());
  EXPECT_FALSE(FileUtil::FileExists(win32::WideToUtf8(sandbox.destination()))
                   .ok());
  EXPECT_TRUE(FileUtil::FileExists(win32::WideToUtf8(state)).ok());
  EXPECT_TRUE(FileUtil::DirectoryExists(win32::WideToUtf8(projects)).ok());
  EXPECT_TRUE(FileUtil::FileExists(win32::WideToUtf8(other)).ok());
  EXPECT_TRUE(registrar.Unregister(kTsfConsumerId).ok());
}

TEST(WindowsConsumerFileRegistrarTest,
     InstallerAppDataRemovesOnlyTheMozkeyConsumerInOssBuild) {
  TempDirectory app_data = testing::MakeTempDirectoryOrDie();
  const absl::StatusOr<std::string> root =
      ResolveWindowsProtocolV1Root(/*override_root=*/"", app_data.path());
  ASSERT_TRUE(root.ok()) << root.status();
  WindowsConsumerFileRegistrar registrar(*root);
  ASSERT_TRUE(registrar.Refresh(Handshake()).ok());
  ConsumerHandshake other = Handshake();
  other.consumer_id = "other-ime";
  other.name = "Another IME";
  ASSERT_TRUE(registrar.Refresh(other).ok());

  ASSERT_TRUE(
      UnregisterWindowsDesktopConsumerForAppData(app_data.path()).ok());
  const std::string own_path = *root + "\\consumers\\tsf-mozkey.json";
  const std::string other_path = *root + "\\consumers\\other-ime.json";
#if defined(MOZC_BUILD)
  EXPECT_FALSE(FileUtil::FileExists(own_path).ok());
#else
  EXPECT_TRUE(FileUtil::FileExists(own_path).ok());
#endif
  EXPECT_TRUE(FileUtil::FileExists(other_path).ok());
}

TEST(WindowsConsumerFileRegistrarTest, RejectsUnsafeIdAndReparsePoint) {
  Sandbox sandbox;
  WindowsConsumerFileRegistrar registrar(sandbox.root_utf8());
  ASSERT_TRUE(registrar.Refresh(Handshake()).ok());
  EXPECT_EQ(registrar.Unregister("../outside").code(),
            absl::StatusCode::kInvalidArgument);

  const std::wstring target = sandbox.consumers() + L"\\target.json";
  ASSERT_TRUE(::MoveFileW(sandbox.destination().c_str(), target.c_str()))
      << ::GetLastError();
  constexpr DWORD kAllowUnprivilegedCreate = 0x2;
  if (!::CreateSymbolicLinkW(sandbox.destination().c_str(), L"target.json",
                             kAllowUnprivilegedCreate)) {
    const DWORD error = ::GetLastError();
    if (error == ERROR_PRIVILEGE_NOT_HELD || error == ERROR_NOT_SUPPORTED ||
        error == ERROR_INVALID_PARAMETER) {
      GTEST_SKIP() << "symbolic-link creation is unavailable: " << error;
    }
    FAIL() << "CreateSymbolicLinkW failed: " << error;
  }
  EXPECT_EQ(registrar.Refresh(Handshake()).code(),
            absl::StatusCode::kPermissionDenied);
  EXPECT_EQ(registrar.Unregister(kTsfConsumerId).code(),
            absl::StatusCode::kPermissionDenied);
}

TEST(WindowsConsumerFileRegistrarTest, RejectsHardLinkedConsumerFile) {
  Sandbox sandbox;
  WindowsConsumerFileRegistrar registrar(sandbox.root_utf8());
  ASSERT_TRUE(registrar.Refresh(Handshake()).ok());
  const std::wstring alias = sandbox.consumers() + L"\\alias.json";
  ASSERT_TRUE(::CreateHardLinkW(alias.c_str(), sandbox.destination().c_str(),
                               nullptr))
      << ::GetLastError();
  EXPECT_EQ(registrar.Refresh(Handshake()).code(),
            absl::StatusCode::kPermissionDenied);
  EXPECT_EQ(registrar.Unregister(kTsfConsumerId).code(),
            absl::StatusCode::kPermissionDenied);
}

TEST(WindowsConsumerFileRegistrarTest, RejectsInvalidRootsBeforeWriting) {
  for (const std::string &root : {R"(..\ime)", R"(C:\safe\..\ime)",
                                  R"(C:\safe\ime\)"}) {
    EXPECT_EQ(WindowsConsumerFileRegistrar(root).Refresh(Handshake()).code(),
              absl::StatusCode::kInvalidArgument)
        << root;
  }
}

TEST(WindowsConsumerFileRegistrarTest, RejectsUncRootInLocalOnlyMode) {
  WindowsConsumerFileRegistrar registrar(R"(\\server\share\ime)");

  EXPECT_EQ(registrar.Refresh(Handshake()).code(),
            absl::StatusCode::kInvalidArgument);
  EXPECT_EQ(registrar.Unregister(kTsfConsumerId).code(),
            absl::StatusCode::kInvalidArgument);
}

}  // namespace
}  // namespace mozc::grimodex

#endif  // defined(_WIN32)
