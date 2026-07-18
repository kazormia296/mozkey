// Copyright 2026 The Mozkey Authors

#include "grimodex/consumer_file_registrar_posix.h"

#if !defined(_WIN32)

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <string>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/str_cat.h"
#include "base/file/temp_dir.h"
#include "base/file_util.h"
#include "grimodex/consumer_handshake.h"
#include "testing/gunit.h"
#include "testing/mozctest.h"

namespace mozc::grimodex {
namespace {

ConsumerHandshake Handshake(absl::string_view id = kImkitConsumerId,
                            absl::string_view timestamp =
                                "2026-07-18T01:02:03.456Z") {
  return ConsumerHandshake{
      .consumer_id = std::string(id),
      .name = "Mozkey for Grimodex on macOS",
      .version = "0.8.0",
      .platform = "macos",
      .last_seen = std::string(timestamp),
      .capabilities = {.profile = true,
                       .dynamic_dictionary = true,
                       .zenzai_v3_conditions = false,
                       .application_scoping = true},
  };
}

mode_t Mode(absl::string_view path) {
  struct stat info = {};
  EXPECT_EQ(stat(std::string(path).c_str(), &info), 0) << path;
  return info.st_mode & 0777;
}

TEST(PosixConsumerFileRegistrarTest, CreatesPrivateCanonicalHeartbeat) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string root = absl::StrCat(temp.path(), "/fresh/ime");
  PosixConsumerFileRegistrar registrar(root);
  const ConsumerHandshake handshake = Handshake();

  ASSERT_TRUE(registrar.Refresh(handshake).ok());
  const std::string consumers = absl::StrCat(root, "/consumers");
  const std::string destination =
      absl::StrCat(consumers, "/", handshake.consumer_id, ".json");
  EXPECT_EQ(Mode(root), 0700);
  EXPECT_EQ(Mode(consumers), 0700);
  EXPECT_EQ(Mode(destination), 0600);
  absl::StatusOr<std::string> expected =
      SerializeConsumerHandshake(handshake);
  ASSERT_TRUE(expected.ok()) << expected.status();
  absl::StatusOr<std::string> actual = FileUtil::GetContents(destination);
  ASSERT_TRUE(actual.ok()) << actual.status();
  EXPECT_EQ(*actual, *expected);
}

TEST(PosixConsumerFileRegistrarTest, RepairsOwnedDirectoryModes) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string root = absl::StrCat(temp.path(), "/ime");
  const std::string consumers = absl::StrCat(root, "/consumers");
  ASSERT_EQ(mkdir(root.c_str(), 0755), 0);
  ASSERT_EQ(mkdir(consumers.c_str(), 0755), 0);
  ASSERT_EQ(chmod(root.c_str(), 0755), 0);
  ASSERT_EQ(chmod(consumers.c_str(), 0755), 0);

  ASSERT_TRUE(PosixConsumerFileRegistrar(root).Refresh(Handshake()).ok());
  EXPECT_EQ(Mode(root), 0700);
  EXPECT_EQ(Mode(consumers), 0700);
}

TEST(PosixConsumerFileRegistrarTest, RefreshAtomicallyReplacesOpenFile) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string root = absl::StrCat(temp.path(), "/ime");
  PosixConsumerFileRegistrar registrar(root);
  const ConsumerHandshake first = Handshake();
  ASSERT_TRUE(registrar.Refresh(first).ok());
  const std::string destination =
      absl::StrCat(root, "/consumers/", first.consumer_id, ".json");
  const int old_fd = open(destination.c_str(), O_RDONLY | O_CLOEXEC);
  ASSERT_GE(old_fd, 0);

  ConsumerHandshake second =
      Handshake(kImkitConsumerId, "2026-07-18T01:17:03.456Z");
  second.version = "0.8.1";
  ASSERT_TRUE(registrar.Refresh(second).ok());

  absl::StatusOr<std::string> current = FileUtil::GetContents(destination);
  absl::StatusOr<std::string> expected_second =
      SerializeConsumerHandshake(second);
  ASSERT_TRUE(current.ok()) << current.status();
  ASSERT_TRUE(expected_second.ok()) << expected_second.status();
  EXPECT_EQ(*current, *expected_second);

  char buffer[kMaxConsumerHandshakeBytes] = {};
  const ssize_t old_size = read(old_fd, buffer, sizeof(buffer));
  ASSERT_GE(old_size, 0);
  ASSERT_EQ(close(old_fd), 0);
  absl::StatusOr<std::string> expected_first =
      SerializeConsumerHandshake(first);
  ASSERT_TRUE(expected_first.ok()) << expected_first.status();
  EXPECT_EQ(std::string(buffer, static_cast<size_t>(old_size)),
            *expected_first);
}

TEST(PosixConsumerFileRegistrarTest,
     PreservesProjectsAndOtherConsumersOnRefreshAndUnregister) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string root = absl::StrCat(temp.path(), "/ime");
  PosixConsumerFileRegistrar registrar(root);
  const ConsumerHandshake handshake = Handshake();
  ASSERT_TRUE(registrar.Refresh(handshake).ok());
  const std::string state = absl::StrCat(root, "/state.json");
  const std::string projects = absl::StrCat(root, "/projects");
  const std::string other =
      absl::StrCat(root, "/consumers/other-ime.json");
  ASSERT_EQ(mkdir(projects.c_str(), 0700), 0);
  ASSERT_TRUE(FileUtil::SetContents(state, "state\n").ok());
  ASSERT_TRUE(FileUtil::SetContents(other, "other\n").ok());
  ASSERT_EQ(chmod(other.c_str(), 0600), 0);

  ASSERT_TRUE(registrar.Refresh(handshake).ok());
  EXPECT_TRUE(FileUtil::FileExists(state).ok());
  EXPECT_TRUE(FileUtil::DirectoryExists(projects).ok());
  EXPECT_TRUE(FileUtil::FileExists(other).ok());
  ASSERT_TRUE(registrar.Unregister(handshake.consumer_id).ok());
  EXPECT_TRUE(FileUtil::FileExists(state).ok());
  EXPECT_TRUE(FileUtil::DirectoryExists(projects).ok());
  EXPECT_TRUE(FileUtil::FileExists(other).ok());
  EXPECT_TRUE(registrar.Unregister(handshake.consumer_id).ok());
}

TEST(PosixConsumerFileRegistrarTest, RejectsUnsafeIdsAndSymlinks) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string target = absl::StrCat(temp.path(), "/target");
  const std::string root_link = absl::StrCat(temp.path(), "/root-link");
  ASSERT_EQ(mkdir(target.c_str(), 0700), 0);
  ASSERT_EQ(symlink(target.c_str(), root_link.c_str()), 0);
  PosixConsumerFileRegistrar linked_root(root_link);
  EXPECT_FALSE(linked_root.Refresh(Handshake()).ok());

  const std::string root = absl::StrCat(temp.path(), "/ime");
  PosixConsumerFileRegistrar registrar(root);
  ASSERT_TRUE(registrar.Refresh(Handshake()).ok());
  const std::string destination =
      absl::StrCat(root, "/consumers/", kImkitConsumerId, ".json");
  ASSERT_EQ(unlink(destination.c_str()), 0);
  ASSERT_EQ(symlink("outside", destination.c_str()), 0);
  EXPECT_EQ(registrar.Refresh(Handshake()).code(),
            absl::StatusCode::kPermissionDenied);
  EXPECT_EQ(registrar.Unregister(kImkitConsumerId).code(),
            absl::StatusCode::kPermissionDenied);
  EXPECT_EQ(registrar.Unregister("../outside").code(),
            absl::StatusCode::kInvalidArgument);
}

TEST(PosixConsumerFileRegistrarTest, RejectsHardLinkedConsumerFile) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string root = absl::StrCat(temp.path(), "/ime");
  PosixConsumerFileRegistrar registrar(root);
  ASSERT_TRUE(registrar.Refresh(Handshake()).ok());
  const std::string destination =
      absl::StrCat(root, "/consumers/", kImkitConsumerId, ".json");
  const std::string alias = absl::StrCat(root, "/consumer-alias.json");
  ASSERT_EQ(link(destination.c_str(), alias.c_str()), 0);
  EXPECT_EQ(registrar.Refresh(Handshake()).code(),
            absl::StatusCode::kPermissionDenied);
  EXPECT_EQ(registrar.Unregister(kImkitConsumerId).code(),
            absl::StatusCode::kPermissionDenied);
}

TEST(PosixConsumerFileRegistrarTest, RejectsNonNormalizedRootAndBadHandshake) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  for (const std::string &root : {
           absl::StrCat(temp.path(), "/ime/"),
           absl::StrCat(temp.path(), "//ime"),
           absl::StrCat(temp.path(), "/./ime"),
           absl::StrCat(temp.path(), "/../ime"),
       }) {
    PosixConsumerFileRegistrar registrar(root);
    EXPECT_EQ(registrar.Refresh(Handshake()).code(),
              absl::StatusCode::kInvalidArgument)
        << root;
  }
  ConsumerHandshake invalid = Handshake();
  invalid.consumer_id = "../outside";
  const std::string root = absl::StrCat(temp.path(), "/never-created");
  EXPECT_EQ(PosixConsumerFileRegistrar(root).Refresh(invalid).code(),
            absl::StatusCode::kInvalidArgument);
  EXPECT_FALSE(FileUtil::DirectoryExists(root).ok());
}

}  // namespace
}  // namespace mozc::grimodex

#endif  // !defined(_WIN32)
