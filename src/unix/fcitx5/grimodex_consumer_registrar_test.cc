// Copyright 2026 The Mozkey Authors

#include "unix/fcitx5/grimodex_consumer_registrar.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <filesystem>
#include <string>
#include <vector>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/str_cat.h"
#include "base/file/temp_dir.h"
#include "base/file_util.h"
#include "google/protobuf/struct.pb.h"
#include "google/protobuf/util/json_util.h"
#include "testing/gunit.h"
#include "testing/mozctest.h"

namespace mozc::fcitx5 {
namespace {

constexpr absl::string_view kTimeA = "2026-07-17T01:02:03.456Z";
constexpr absl::string_view kTimeB = "2026-07-17T01:17:03.456Z";

mode_t Mode(absl::string_view path) {
  struct stat info = {};
  EXPECT_EQ(stat(std::string(path).c_str(), &info), 0) << path;
  return info.st_mode & 0777;
}

google::protobuf::Struct ReadJson(absl::string_view path) {
  absl::StatusOr<std::string> bytes = FileUtil::GetContents(path);
  EXPECT_TRUE(bytes.ok()) << bytes.status();
  google::protobuf::Struct value;
  if (bytes.ok()) {
    EXPECT_TRUE(google::protobuf::util::JsonStringToMessage(*bytes, &value).ok());
  }
  return value;
}

std::string StringField(const google::protobuf::Struct &value,
                        absl::string_view key) {
  const auto iterator = value.fields().find(std::string(key));
  EXPECT_NE(iterator, value.fields().end()) << key;
  return iterator == value.fields().end() ? std::string()
                                          : iterator->second.string_value();
}

std::vector<std::string> ListEntries(absl::string_view path) {
  std::vector<std::string> entries;
  for (const std::filesystem::directory_entry &entry :
       std::filesystem::directory_iterator(std::string(path))) {
    entries.push_back(entry.path().filename().string());
  }
  std::sort(entries.begin(), entries.end());
  return entries;
}

TEST(GrimodexConsumerRegistrarTest, CreatesPrivateAtomicHandshake) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string root = absl::StrCat(temp.path(), "/fresh/ime");
  GrimodexConsumerRegistrar registrar(root);

  EXPECT_TRUE(registrar.Register("v0.7.7", kTimeA).ok());

  const std::string consumers = absl::StrCat(root, "/consumers");
  const std::string destination =
      absl::StrCat(consumers, "/fcitx5-mozkey.json");
  EXPECT_EQ(Mode(root), 0700);
  EXPECT_EQ(Mode(consumers), 0700);
  EXPECT_EQ(Mode(destination), 0600);

  const google::protobuf::Struct value = ReadJson(destination);
  EXPECT_EQ(value.fields().at("format_version").number_value(), 1);
  EXPECT_EQ(StringField(value, "consumer_id"), "fcitx5-mozkey");
  EXPECT_EQ(StringField(value, "name"), "Mozkey for Grimodex on Linux");
  EXPECT_EQ(StringField(value, "version"), "v0.7.7");
  EXPECT_EQ(StringField(value, "platform"), "linux");
  EXPECT_EQ(StringField(value, "last_seen"), kTimeA);
  const auto &capabilities =
      value.fields().at("capabilities").struct_value().fields();
  EXPECT_TRUE(capabilities.at("profile").bool_value());
  EXPECT_TRUE(capabilities.at("dynamic_dictionary").bool_value());
  EXPECT_TRUE(capabilities.at("zenzai_v3_conditions").bool_value());
  EXPECT_TRUE(capabilities.at("application_scoping").bool_value());

  EXPECT_EQ(ListEntries(consumers),
            std::vector<std::string>({"fcitx5-mozkey.json"}));
}

TEST(GrimodexConsumerRegistrarTest, RefreshReplacesOnlyItsOwnFile) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string root = absl::StrCat(temp.path(), "/ime");
  GrimodexConsumerRegistrar registrar(root);
  ASSERT_TRUE(registrar.Register("v0.7.7", kTimeA).ok());

  const std::string consumers = absl::StrCat(root, "/consumers");
  const std::string other = absl::StrCat(consumers, "/other-ime.json");
  ASSERT_TRUE(FileUtil::SetContents(other, "other\n").ok());
  ASSERT_EQ(chmod(other.c_str(), 0600), 0);

  EXPECT_TRUE(registrar.Register("v0.7.8", kTimeB).ok());

  const std::string destination =
      absl::StrCat(consumers, "/fcitx5-mozkey.json");
  const google::protobuf::Struct value = ReadJson(destination);
  EXPECT_EQ(StringField(value, "version"), "v0.7.8");
  EXPECT_EQ(StringField(value, "last_seen"), kTimeB);
  absl::StatusOr<std::string> other_bytes = FileUtil::GetContents(other);
  ASSERT_TRUE(other_bytes.ok()) << other_bytes.status();
  EXPECT_EQ(*other_bytes, "other\n");

  EXPECT_EQ(ListEntries(consumers),
            std::vector<std::string>({"fcitx5-mozkey.json",
                                      "other-ime.json"}));
}

TEST(GrimodexConsumerRegistrarTest,
     RuntimeMarkerControlsHeartbeatAndZenzRequiresCompleteRuntime) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string root = absl::StrCat(temp.path(), "/ime");
  const std::string marker = absl::StrCat(temp.path(), "/mozc_server");
  const std::string destination =
      absl::StrCat(root, "/consumers/fcitx5-mozkey.json");
  GrimodexConsumerRegistrar registrar(root);

  // An addon mapped after package removal must not advertise itself.
  EXPECT_TRUE(registrar.RefreshIfInstalled("v0.7.7", kTimeA, marker).ok());
  EXPECT_FALSE(FileUtil::FileExists(destination).ok());

  ASSERT_TRUE(FileUtil::SetContents(marker, "runtime\n").ok());
  ASSERT_EQ(chmod(marker.c_str(), 0755), 0);
  EXPECT_TRUE(registrar.RefreshIfInstalled("v0.7.7", kTimeA, marker).ok());
  EXPECT_TRUE(FileUtil::FileExists(destination).ok());
  EXPECT_FALSE(ReadJson(destination)
                   .fields()
                   .at("capabilities")
                   .struct_value()
                   .fields()
                   .at("zenzai_v3_conditions")
                   .bool_value());

  const std::string scorer = absl::StrCat(temp.path(), "/mozc_zenz_scorer");
  const std::string model_directory = absl::StrCat(temp.path(), "/models");
  const std::string model =
      absl::StrCat(model_directory, "/zenz-v3.2-small-Q5_K_M.gguf");
  const std::string llama_server =
      absl::StrCat(temp.path(), "/llama-server");
  ASSERT_EQ(mkdir(model_directory.c_str(), 0700), 0);
  ASSERT_TRUE(FileUtil::SetContents(scorer, "scorer\n").ok());
  ASSERT_TRUE(FileUtil::SetContents(model, "gguf\n").ok());
  ASSERT_TRUE(FileUtil::SetContents(llama_server, "runtime\n").ok());
  ASSERT_EQ(chmod(scorer.c_str(), 0755), 0);
  ASSERT_EQ(chmod(model.c_str(), 0644), 0);
  ASSERT_EQ(chmod(llama_server.c_str(), 0755), 0);
  EXPECT_TRUE(registrar.RefreshIfInstalled("v0.7.7", kTimeB, marker).ok());
  EXPECT_TRUE(ReadJson(destination)
                  .fields()
                  .at("capabilities")
                  .struct_value()
                  .fields()
                  .at("zenzai_v3_conditions")
                  .bool_value());

  ASSERT_EQ(unlink(marker.c_str()), 0);
  EXPECT_TRUE(registrar.RefreshIfInstalled("v0.7.7", kTimeB, marker).ok());
  EXPECT_FALSE(FileUtil::FileExists(destination).ok());
  EXPECT_TRUE(registrar.RefreshIfInstalled("v0.7.7", kTimeB, marker).ok());
  EXPECT_FALSE(FileUtil::FileExists(destination).ok());
}

TEST(GrimodexConsumerRegistrarTest, RejectsSymlinkRuntimeMarker) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string root = absl::StrCat(temp.path(), "/ime");
  const std::string executable = absl::StrCat(temp.path(), "/server-real");
  const std::string marker = absl::StrCat(temp.path(), "/server-link");
  ASSERT_TRUE(FileUtil::SetContents(executable, "runtime\n").ok());
  ASSERT_EQ(chmod(executable.c_str(), 0755), 0);
  ASSERT_EQ(symlink(executable.c_str(), marker.c_str()), 0);

  GrimodexConsumerRegistrar registrar(root);
  EXPECT_EQ(registrar.RefreshIfInstalled("v0.7.7", kTimeA, marker).code(),
            absl::StatusCode::kFailedPrecondition);
  EXPECT_FALSE(FileUtil::FileExists(
                   absl::StrCat(root, "/consumers/fcitx5-mozkey.json"))
                   .ok());
}

TEST(GrimodexConsumerRegistrarTest, RejectsUnsafeInputsBeforeWriting) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string root = absl::StrCat(temp.path(), "/ime");
  GrimodexConsumerRegistrar registrar(root);

  EXPECT_EQ(registrar.Register("bad\"version", kTimeA).code(),
            absl::StatusCode::kInvalidArgument);
  EXPECT_EQ(registrar.Register("v0.7.7", "not-a-time").code(),
            absl::StatusCode::kInvalidArgument);
  EXPECT_FALSE(FileUtil::DirectoryExists(root).ok());
}

TEST(GrimodexConsumerRegistrarTest, RejectsSymlinkedSecurityBoundary) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string target = absl::StrCat(temp.path(), "/target");
  const std::string root = absl::StrCat(temp.path(), "/ime");
  ASSERT_EQ(mkdir(target.c_str(), 0700), 0);
  ASSERT_EQ(symlink(target.c_str(), root.c_str()), 0);

  GrimodexConsumerRegistrar registrar(root);
  EXPECT_FALSE(registrar.Register("v0.7.7", kTimeA).ok());
  EXPECT_FALSE(FileUtil::FileExists(
                   absl::StrCat(target, "/consumers/fcitx5-mozkey.json"))
                   .ok());
}

TEST(GrimodexConsumerRegistrarTest, RejectsSymlinkedAncestorComponent) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string target = absl::StrCat(temp.path(), "/target");
  const std::string alias = absl::StrCat(temp.path(), "/alias");
  ASSERT_EQ(mkdir(target.c_str(), 0700), 0);
  ASSERT_EQ(symlink(target.c_str(), alias.c_str()), 0);

  GrimodexConsumerRegistrar registrar(absl::StrCat(alias, "/ime"));
  EXPECT_FALSE(registrar.Register("v0.7.7", kTimeA).ok());
  EXPECT_FALSE(FileUtil::FileExists(
                   absl::StrCat(target, "/ime/consumers/fcitx5-mozkey.json"))
                   .ok());
}

TEST(GrimodexConsumerRegistrarTest, RejectsNonNormalizedRoot) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  for (const std::string &root : {
           absl::StrCat(temp.path(), "/ime/"),
           absl::StrCat(temp.path(), "//ime"),
           absl::StrCat(temp.path(), "/./ime"),
           absl::StrCat(temp.path(), "/../ime"),
       }) {
    GrimodexConsumerRegistrar registrar(root);
    EXPECT_EQ(registrar.Register("v0.7.7", kTimeA).code(),
              absl::StatusCode::kInvalidArgument)
        << root;
  }
}

TEST(GrimodexConsumerRegistrarTest, UnregisterPreservesOtherStateAndConsumers) {
  TempDirectory temp = testing::MakeTempDirectoryOrDie();
  const std::string root = absl::StrCat(temp.path(), "/ime");
  GrimodexConsumerRegistrar registrar(root);
  ASSERT_TRUE(registrar.Register("v0.7.7", kTimeA).ok());

  const std::string state = absl::StrCat(root, "/state.json");
  const std::string other = absl::StrCat(root, "/consumers/other-ime.json");
  ASSERT_TRUE(FileUtil::SetContents(state, "state\n").ok());
  ASSERT_TRUE(FileUtil::SetContents(other, "other\n").ok());

  EXPECT_TRUE(registrar.Unregister().ok());
  EXPECT_FALSE(FileUtil::FileExists(
                   absl::StrCat(root, "/consumers/fcitx5-mozkey.json"))
                   .ok());
  EXPECT_TRUE(FileUtil::FileExists(state).ok());
  EXPECT_TRUE(FileUtil::FileExists(other).ok());
  EXPECT_TRUE(registrar.Unregister().ok());
}

}  // namespace
}  // namespace mozc::fcitx5
