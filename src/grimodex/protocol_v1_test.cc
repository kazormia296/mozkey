// Copyright 2026 The Mozkey Authors

#include "grimodex/protocol_v1.h"

#include <cstddef>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/str_cat.h"
#include "base/file_util.h"
#include "testing/gunit.h"
#include "testing/mozctest.h"

namespace mozc::grimodex {
namespace {

constexpr char kStateA[] = R"json({
  "format_version": 1,
  "active_project_id": "project-a",
  "updated_at": "2026-07-11T00:00:00.000Z"
})json";

constexpr char kStateB[] = R"json({
  "format_version": 1,
  "active_project_id": "project-a",
  "updated_at": "2026-07-11T00:00:01.000Z"
})json";

std::string MinimalProject(absl::string_view project_id = "project-a",
                           absl::string_view generated_at =
                               "2026-07-11T00:00:00.000Z",
                           absl::string_view surface = "刹那") {
  return absl::StrCat(R"json({
  "format_version": 1,
  "project_id": ")json",
                      project_id, R"json(",
  "project_name": "星海年代記",
  "generated_at": ")json",
                      generated_at, R"json(",
  "entries": [{
    "yomi": "せつな",
    "surface": ")json",
                      surface, R"json(",
    "category": "person",
    "priority": 2,
    "entry_id": "entry-setsuna"
  }]
})json");
}

std::string Fixture(absl::string_view relative_path) {
  const std::string path = testing::GetSourceFileOrDie(
      {"grimodex", "testdata", "protocol_v1", relative_path});
  absl::StatusOr<std::string> contents = FileUtil::GetContents(path);
  EXPECT_TRUE(contents.ok()) << contents.status();
  return contents.ok() ? *contents : std::string();
}

class ScriptedReader final : public ProtocolV1FileReader {
 public:
  ScriptedReader(std::vector<std::string> states,
                 std::vector<std::string> projects)
      : states_(std::move(states)), projects_(std::move(projects)) {}

  absl::StatusOr<VerifiedFileBytes> ReadState(size_t max_bytes) override {
    ++state_reads_;
    if (state_index_ >= states_.size()) {
      return absl::NotFoundError("scripted state exhausted");
    }
    std::string bytes = states_[state_index_++];
    if (bytes.size() > max_bytes) {
      return absl::ResourceExhaustedError("scripted state oversized");
    }
    return VerifiedFileBytes::FromBytes(std::move(bytes));
  }

  absl::StatusOr<VerifiedFileBytes> ReadProject(
      absl::string_view project_id, size_t max_bytes) override {
    ++project_reads_;
    last_project_id_ = std::string(project_id);
    if (project_index_ >= projects_.size()) {
      return absl::NotFoundError("scripted project exhausted");
    }
    std::string bytes = projects_[project_index_++];
    if (bytes.size() > max_bytes) {
      return absl::ResourceExhaustedError("scripted project oversized");
    }
    return VerifiedFileBytes::FromBytes(std::move(bytes));
  }

  size_t state_reads() const { return state_reads_; }
  size_t project_reads() const { return project_reads_; }
  const std::string &last_project_id() const { return last_project_id_; }

 private:
  std::vector<std::string> states_;
  std::vector<std::string> projects_;
  size_t state_index_ = 0;
  size_t project_index_ = 0;
  size_t state_reads_ = 0;
  size_t project_reads_ = 0;
  std::string last_project_id_;
};

std::shared_ptr<ScriptedReader> ReaderFor(
    std::string state, std::optional<std::string> project = std::nullopt) {
  std::vector<std::string> states = {state, std::move(state)};
  std::vector<std::string> projects;
  if (project.has_value()) {
    projects.push_back(std::move(*project));
  }
  return std::make_shared<ScriptedReader>(std::move(states),
                                          std::move(projects));
}

TEST(ProtocolV1Test, ComputesStandardSha256Digest) {
  EXPECT_EQ(VerifiedFileBytes::FromBytes("abc").sha256,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}

TEST(ProtocolV1Test, LoadsSharedValidFixtureIntoIndependentDto) {
  ProtocolV1Loader loader(ReaderFor(
      Fixture("valid/state-active.json"),
      Fixture("valid/project-with-zenzai-context.json")));
  const LoadResult result = loader.Load();

  ASSERT_EQ(result.diagnostic, LoadDiagnostic::kLoaded);
  ASSERT_NE(result.snapshot, nullptr);
  EXPECT_EQ(result.snapshot->project_id, "project-a");
  EXPECT_EQ(result.snapshot->project_name, "星海年代記");
  ASSERT_EQ(result.snapshot->entries.size(), 1);
  EXPECT_EQ(result.snapshot->entries[0].yomi, "せつな");
  EXPECT_EQ(result.snapshot->entries[0].surface, "刹那");
  EXPECT_EQ(result.snapshot->entries[0].category,
            DictionaryCategory::kPerson);
  EXPECT_EQ(result.snapshot->entries[0].priority, 2);
  EXPECT_EQ(result.snapshot->entries[0].entry_id, "entry-setsuna");
  EXPECT_EQ(result.snapshot->conditions.topic,
            "星海年代記・軍事SF・宇宙植民地を舞台にした物語");
  EXPECT_EQ(result.snapshot->conditions.style, std::nullopt);
  EXPECT_EQ(result.snapshot->conditions.preference, std::nullopt);
  EXPECT_EQ(result.snapshot->state_sha256.size(), 64);
  EXPECT_EQ(result.snapshot->project_sha256.size(), 64);
}

TEST(ProtocolV1Test, ExplicitNullActiveProjectIsInactive) {
  ProtocolV1Loader loader(ReaderFor(Fixture("valid/state-inactive.json")));
  const LoadResult result = loader.Load();

  EXPECT_EQ(result.diagnostic, LoadDiagnostic::kInactive);
  EXPECT_EQ(result.snapshot, nullptr);
}

TEST(ProtocolV1Test, InvalidAndMaliciousFixturesFailClosed) {
  for (absl::string_view state_fixture : {
           "invalid/state-unsupported-version.json",
           "malicious/state-path-traversal.json",
       }) {
    ProtocolV1Loader loader(ReaderFor(Fixture(state_fixture)));
    EXPECT_EQ(loader.Load().diagnostic, LoadDiagnostic::kInvalidState)
        << state_fixture;
  }

  for (absl::string_view project_fixture : {
           "invalid/project-unsupported-version.json",
           "invalid/project-unknown-category.json",
           "malicious/project-path-traversal.json",
           "malicious/project-control-character.json",
       }) {
    ProtocolV1Loader loader(ReaderFor(
        Fixture("valid/state-active.json"), Fixture(project_fixture)));
    EXPECT_EQ(loader.Load().diagnostic, LoadDiagnostic::kInvalidSnapshot)
        << project_fixture;
  }
}

TEST(ProtocolV1Test, StrictlyRequiresEveryProtocolV1ContractKey) {
  {
    ProtocolV1Loader loader(ReaderFor(R"json({
      "format_version": 1,
      "updated_at": "2026-07-11T00:00:00.000Z"
    })json"));
    EXPECT_EQ(loader.Load().diagnostic, LoadDiagnostic::kInvalidState);
  }
  {
    ProtocolV1Loader loader(ReaderFor(kStateA, R"json({
      "format_version": 1,
      "project_id": "project-a",
      "project_name": "Missing entries",
      "generated_at": "2026-07-11T00:00:00.000Z"
    })json"));
    EXPECT_EQ(loader.Load().diagnostic, LoadDiagnostic::kInvalidSnapshot);
  }
  {
    ProtocolV1Loader loader(ReaderFor(kStateA, R"json({
      "format_version": 1,
      "project_id": "project-a",
      "project_name": "Missing nullable context key",
      "generated_at": "2026-07-11T00:00:00.000Z",
      "entries": [],
      "zenzai_context": {
        "topic": "topic",
        "preference": null
      }
    })json"));
    EXPECT_EQ(loader.Load().diagnostic, LoadDiagnostic::kInvalidSnapshot);
  }
}

TEST(ProtocolV1Test, RejectsOversizedStateAndProjectFiles) {
  {
    ProtocolV1Loader loader(ReaderFor(
        std::string(ProtocolV1Limits::kStateBytes + 1, ' ')));
    EXPECT_EQ(loader.Load().diagnostic, LoadDiagnostic::kInvalidState);
  }
  {
    ProtocolV1Loader loader(ReaderFor(
        Fixture("valid/state-active.json"),
        std::string(ProtocolV1Limits::kProjectBytes + 1, ' ')));
    EXPECT_EQ(loader.Load().diagnostic, LoadDiagnostic::kInvalidSnapshot);
  }
}

TEST(ProtocolV1Test, RequiresExactStateBytesAcrossProjectReadForSameProjectId) {
  auto reader = std::make_shared<ScriptedReader>(
      std::vector<std::string>{kStateA, kStateB},
      std::vector<std::string>{MinimalProject()});
  ProtocolV1Loader loader(reader,
                          ProtocolV1Loader::Options{.stale_retries = 0});

  const LoadResult result = loader.Load();

  EXPECT_EQ(result.diagnostic, LoadDiagnostic::kStateChangedDuringRead);
  EXPECT_EQ(result.snapshot, nullptr);
  EXPECT_EQ(reader->state_reads(), 2);
  EXPECT_EQ(reader->project_reads(), 1);
  EXPECT_EQ(reader->last_project_id(), "project-a");
}

TEST(ProtocolV1Test, RetriesAStaleReadAndPublishesOnlyTheCoherentAttempt) {
  auto reader = std::make_shared<ScriptedReader>(
      std::vector<std::string>{kStateA, kStateB, kStateB, kStateB},
      std::vector<std::string>{
          MinimalProject("project-a", "2026-07-11T00:00:00.000Z", "旧稿"),
          MinimalProject("project-a", "2026-07-11T00:00:01.000Z", "確定稿"),
      });
  ProtocolV1Loader loader(reader,
                          ProtocolV1Loader::Options{.stale_retries = 1});

  const LoadResult result = loader.Load();

  ASSERT_EQ(result.diagnostic, LoadDiagnostic::kLoaded);
  ASSERT_NE(result.snapshot, nullptr);
  ASSERT_EQ(result.snapshot->entries.size(), 1);
  EXPECT_EQ(result.snapshot->entries[0].surface, "確定稿");
  EXPECT_EQ(result.snapshot->state_updated_at,
            "2026-07-11T00:00:01.000Z");
  EXPECT_EQ(reader->state_reads(), 4);
  EXPECT_EQ(reader->project_reads(), 2);
}

TEST(ProtocolV1Test, SharedCrossPlatformFixturesRemainDigestLocked) {
  struct FixtureDigest {
    absl::string_view path;
    absl::string_view sha256;
  };
  constexpr FixtureDigest kFixtures[] = {
      {"valid/state-active.json",
       "c1e2fe0c8dfa1cd73ee0005a4c5f099d0c0a6f00f4a76fed811d711833d6205c"},
      {"valid/state-inactive.json",
       "7ee8e51de1d3efdc1b7ba88092cb67a62269033b73565ee16ac971959133704c"},
      {"valid/project-with-zenzai-context.json",
       "62f8936909439d0cd258ccdd65084ae111f5c798f5a014a3cc1e07578e63379f"},
      {"invalid/project-unknown-category.json",
       "9621c8847498daadd7165551efbfd7c93bd3bdf279f2c7a0a77812b22cba16ef"},
      {"invalid/project-unsupported-version.json",
       "96c44501fd207b0e197297e1e90309d5fd941dcbba3916b65fdf877bdeeb3ac0"},
      {"invalid/state-unsupported-version.json",
       "8e6ba85df2b3fa23e242a58ea15efe73c84dcf4ee44a71983bba78ccd5ab8160"},
      {"malicious/project-control-character.json",
       "15367d8dc09700535c87b95379d0eee24e0e84f9b9f634ccc549e6762ed5ad89"},
      {"malicious/project-path-traversal.json",
       "b49c90afa15e02db95d44ca91baa1ef1c49b844582247cc4b60e83f98fd5e6bf"},
      {"malicious/state-path-traversal.json",
       "ea7069391951b762eee6465cd1dcea61964857f31d3a1685ee6becb524c88fcf"},
  };

  for (const FixtureDigest& fixture : kFixtures) {
    EXPECT_EQ(VerifiedFileBytes::FromBytes(Fixture(fixture.path)).sha256,
              fixture.sha256)
        << fixture.path;
  }
}

TEST(ProtocolV1Test, PublisherUsesImmutableSnapshotsAndSemanticSequence) {
  auto reader = std::make_shared<ScriptedReader>(
      std::vector<std::string>{kStateA, kStateA, kStateA, kStateA,
                               kStateA, kStateA},
      std::vector<std::string>{
          MinimalProject(),
          MinimalProject("project-a", "2026-07-11T00:00:01.000Z"),
          MinimalProject("project-a", "2026-07-11T00:00:02.000Z",
                         "刹那改"),
      });
  auto loader = std::make_shared<ProtocolV1Loader>(reader);
  ProtocolV1SnapshotPublisher publisher(loader);

  const std::shared_ptr<const PublishedProtocolV1Snapshot> first =
      publisher.Reload();
  ASSERT_EQ(first->diagnostic, LoadDiagnostic::kLoaded);
  ASSERT_NE(first->snapshot, nullptr);
  EXPECT_EQ(first->sequence, 1);
  EXPECT_EQ(first->snapshot->entries[0].surface, "刹那");
  const std::string first_digest = first->snapshot->project_sha256;

  const std::shared_ptr<const PublishedProtocolV1Snapshot> timestamp_only =
      publisher.Reload();
  EXPECT_EQ(timestamp_only->sequence, 1);
  EXPECT_NE(timestamp_only->snapshot->project_sha256, first_digest);
  EXPECT_EQ(first->snapshot->project_sha256, first_digest);

  const std::shared_ptr<const PublishedProtocolV1Snapshot> changed =
      publisher.Reload();
  EXPECT_EQ(changed->sequence, 2);
  EXPECT_EQ(changed->snapshot->entries[0].surface, "刹那改");
  EXPECT_EQ(first->snapshot->entries[0].surface, "刹那");
  EXPECT_EQ(publisher.Latest(), changed);
}

TEST(ProtocolV1Test, PublisherRetainsOneRetryableFailureThenFailsClosed) {
  auto reader = std::make_shared<ScriptedReader>(
      std::vector<std::string>{kStateA, kStateA, kStateA, kStateA},
      std::vector<std::string>{MinimalProject()});
  auto loader = std::make_shared<ProtocolV1Loader>(
      reader, ProtocolV1Loader::Options{.stale_retries = 0});
  ProtocolV1SnapshotPublisher publisher(loader);

  const std::shared_ptr<const PublishedProtocolV1Snapshot> loaded =
      publisher.Reload();
  ASSERT_EQ(loaded->diagnostic, LoadDiagnostic::kLoaded);
  ASSERT_NE(loaded->snapshot, nullptr);
  EXPECT_EQ(loaded->sequence, 1);

  const std::shared_ptr<const PublishedProtocolV1Snapshot> first_missing =
      publisher.Reload();
  EXPECT_EQ(first_missing->diagnostic, LoadDiagnostic::kMissingSnapshot);
  EXPECT_EQ(first_missing->snapshot, loaded->snapshot);
  EXPECT_EQ(first_missing->sequence, 1);

  const std::shared_ptr<const PublishedProtocolV1Snapshot> second_missing =
      publisher.Reload();
  EXPECT_EQ(second_missing->diagnostic, LoadDiagnostic::kMissingSnapshot);
  EXPECT_EQ(second_missing->snapshot, nullptr);
  EXPECT_EQ(second_missing->sequence, 2);
}

}  // namespace
}  // namespace mozc::grimodex
