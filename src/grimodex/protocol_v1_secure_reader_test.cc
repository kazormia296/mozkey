// Copyright 2026 The Mozkey Authors

#include "grimodex/protocol_v1_secure_reader.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cstddef>
#include <memory>
#include <string>

#include "absl/status/statusor.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/string_view.h"
#include "base/file/temp_dir.h"
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

void WriteAll(absl::string_view path, absl::string_view bytes,
              mode_t mode = 0600) {
  const std::string path_string(path);
  const int fd = open(path_string.c_str(),
                      O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, mode);
  ASSERT_GE(fd, 0) << path;
  size_t offset = 0;
  while (offset < bytes.size()) {
    const ssize_t count =
        write(fd, bytes.data() + offset, bytes.size() - offset);
    ASSERT_GT(count, 0) << path;
    offset += static_cast<size_t>(count);
  }
  ASSERT_EQ(close(fd), 0);
  ASSERT_EQ(chmod(path_string.c_str(), mode), 0);
}

class Sandbox final {
 public:
  Sandbox()
      : temp_(testing::MakeTempDirectoryOrDie()),
        root_(absl::StrCat(temp_.path(), "/ime")),
        projects_(absl::StrCat(root_, "/projects")) {
    EXPECT_EQ(mkdir(root_.c_str(), 0700), 0);
    EXPECT_EQ(mkdir(projects_.c_str(), 0700), 0);
    EXPECT_EQ(chmod(root_.c_str(), 0700), 0);
    EXPECT_EQ(chmod(projects_.c_str(), 0700), 0);
  }

  const std::string &root() const { return root_; }
  const std::string &projects() const { return projects_; }

  std::string state_path() const { return absl::StrCat(root_, "/state.json"); }
  std::string project_path() const {
    return absl::StrCat(projects_, "/project-a.json");
  }

  void InstallState(absl::string_view bytes) const {
    WriteAll(state_path(), bytes);
  }
  void InstallProject(absl::string_view bytes) const {
    WriteAll(project_path(), bytes);
  }

  std::shared_ptr<SecureProtocolV1FileReader> Reader() const {
    return std::make_shared<SecureProtocolV1FileReader>(root_);
  }

 private:
  TempDirectory temp_;
  std::string root_;
  std::string projects_;
};

TEST(ProtocolV1SecureReaderTest, ResolvesOverrideThenPlatformDefault) {
  EXPECT_EQ(ResolveProtocolV1Root("/custom/ime", "/xdg/data", "/home/tester"),
            "/custom/ime");
#if defined(__APPLE__)
  EXPECT_EQ(ResolveProtocolV1Root("", "/xdg/data", "/Users/tester"),
            "/Users/tester/Library/Application Support/"
            "com.miyakey.grimodex/ime");
  EXPECT_EQ(ResolveProtocolV1Root("", "", "/Users/tester"),
            "/Users/tester/Library/Application Support/"
            "com.miyakey.grimodex/ime");
#else   // __APPLE__
  EXPECT_EQ(ResolveProtocolV1Root("", "/xdg/data", "/home/tester"),
            "/xdg/data/com.miyakey.grimodex/ime");
  EXPECT_EQ(ResolveProtocolV1Root("", "", "/home/tester"),
            "/home/tester/.local/share/com.miyakey.grimodex/ime");
#endif  // __APPLE__
}

TEST(ProtocolV1SecureReaderTest,
     RejectsSymlinkFifoAndUnsafeModesWithoutBlocking) {
  {
    Sandbox sandbox;
    const std::string target = absl::StrCat(sandbox.root(), "/target.json");
    WriteAll(target, kState);
    ASSERT_EQ(symlink("target.json", sandbox.state_path().c_str()), 0);
    ProtocolV1Loader loader(sandbox.Reader());
    EXPECT_EQ(loader.Load().diagnostic, LoadDiagnostic::kInvalidState);
  }
  {
    Sandbox sandbox;
    ASSERT_EQ(mkfifo(sandbox.state_path().c_str(), 0600), 0);
    ProtocolV1Loader loader(sandbox.Reader());
    EXPECT_EQ(loader.Load().diagnostic, LoadDiagnostic::kInvalidState);
  }
  {
    Sandbox sandbox;
    sandbox.InstallState(kState);
    ASSERT_EQ(chmod(sandbox.state_path().c_str(), 0640), 0);
    ProtocolV1Loader loader(sandbox.Reader());
    EXPECT_EQ(loader.Load().diagnostic, LoadDiagnostic::kInvalidState);
  }
  {
    Sandbox sandbox;
    sandbox.InstallState(kState);
    sandbox.InstallProject(kProject);
    ASSERT_EQ(chmod(sandbox.projects().c_str(), 0750), 0);
    ProtocolV1Loader loader(sandbox.Reader());
    EXPECT_EQ(loader.Load().diagnostic, LoadDiagnostic::kInvalidSnapshot);
  }
}

TEST(ProtocolV1SecureReaderTest, RejectsSymlinkedRootAndRootPathTraversal) {
  Sandbox sandbox;
  sandbox.InstallState(kState);
  sandbox.InstallProject(kProject);
  const std::string alias = absl::StrCat(sandbox.root(), "-alias");
  ASSERT_EQ(symlink(sandbox.root().c_str(), alias.c_str()), 0);

  ProtocolV1Loader symlink_loader(
      std::make_shared<SecureProtocolV1FileReader>(alias));
  EXPECT_EQ(symlink_loader.Load().diagnostic, LoadDiagnostic::kInvalidState);

  ProtocolV1Loader traversal_loader(
      std::make_shared<SecureProtocolV1FileReader>(
          absl::StrCat(sandbox.root(), "/../ime")));
  EXPECT_EQ(traversal_loader.Load().diagnostic,
            LoadDiagnostic::kInvalidState);
}

TEST(ProtocolV1SecureReaderTest, DefendsProjectPathIndependently) {
  Sandbox sandbox;
  absl::StatusOr<VerifiedFileBytes> result =
      sandbox.Reader()->ReadProject("../outside", 100);
  EXPECT_FALSE(result.ok());
  EXPECT_EQ(result.status().code(), absl::StatusCode::kInvalidArgument);
}

TEST(ProtocolV1SecureReaderTest, RejectsOversizedAndHardLinkedFiles) {
  {
    Sandbox sandbox;
    sandbox.InstallState(kState);
    absl::StatusOr<VerifiedFileBytes> result =
        sandbox.Reader()->ReadState(sizeof(kState) - 2);
    EXPECT_FALSE(result.ok());
    EXPECT_EQ(result.status().code(), absl::StatusCode::kResourceExhausted);
  }
  {
    Sandbox sandbox;
    sandbox.InstallState(kState);
    const std::string alias = absl::StrCat(sandbox.root(), "/state-alias.json");
    ASSERT_EQ(link(sandbox.state_path().c_str(), alias.c_str()), 0);
    absl::StatusOr<VerifiedFileBytes> result =
        sandbox.Reader()->ReadState(sizeof(kState));
    EXPECT_FALSE(result.ok());
    EXPECT_EQ(result.status().code(), absl::StatusCode::kPermissionDenied);
  }
}

}  // namespace
}  // namespace mozc::grimodex
