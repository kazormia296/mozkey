// Copyright 2026 The Mozkey Authors

#include "grimodex/project_dictionary_provider.h"

#include <memory>
#include <string>
#include <utility>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/string_view.h"
#include "dictionary/project_dictionary.h"
#include "grimodex/project_dictionary_bridge.h"
#include "grimodex/protocol_v1.h"
#include "testing/gunit.h"

namespace mozc::grimodex {
namespace {

constexpr char kState[] = R"json({
  "format_version": 1,
  "active_project_id": "project-a",
  "updated_at": "2026-07-17T00:00:00.000Z"
})json";

std::string Project(absl::string_view generated_at,
                    absl::string_view surface) {
  return absl::StrCat(R"json({
  "format_version": 1,
  "project_id": "project-a",
  "project_name": "Project A",
  "generated_at": ")json",
                      generated_at, R"json(",
  "entries": [{
    "yomi": "せつな",
    "surface": ")json",
                      surface, R"json(",
    "category": "person",
    "priority": 2,
    "entry_id": "setsuna"
  }],
  "zenzai_context": {
    "topic": "space opera",
    "style": "formal",
    "preference": "project names first"
  }
})json");
}

class MutableReader final : public ProtocolV1FileReader {
 public:
  explicit MutableReader(std::string project)
      : project_(std::move(project)) {}

  absl::StatusOr<VerifiedFileBytes> ReadState(size_t) override {
    return VerifiedFileBytes::FromBytes(kState);
  }

  absl::StatusOr<VerifiedFileBytes> ReadProject(absl::string_view,
                                                size_t) override {
    if (missing_) {
      return absl::NotFoundError("project missing");
    }
    return VerifiedFileBytes::FromBytes(project_);
  }

  void set_project(std::string project) { project_ = std::move(project); }
  void set_missing(bool missing) { missing_ = missing; }

 private:
  std::string project_;
  bool missing_ = false;
};

constexpr ProjectDictionaryPosIds kPosIds = {
    .person = {.lid = 10, .rid = 10},
    .place = {.lid = 20, .rid = 20},
    .noun = {.lid = 30, .rid = 30},
};

TEST(ProjectDictionaryProviderTest,
     RevalidatesEachCompositionCachesSemanticGenerationAndFailsClosed) {
  auto reader = std::make_shared<MutableReader>(
      Project("2026-07-17T00:00:00.000Z", "刹那"));
  auto loader = std::make_shared<ProtocolV1Loader>(reader);
  auto publisher = std::make_shared<ProtocolV1SnapshotPublisher>(loader);
  ProtocolV1ProjectDictionaryProvider provider(publisher, kPosIds);

  const dictionary::ProjectDictionaryPublication first = provider.Reload();
  ASSERT_NE(first.snapshot, nullptr);
  EXPECT_FALSE(first.clear);
  EXPECT_EQ(first.snapshot->generation(), 1);
  EXPECT_EQ(first.snapshot->metadata().topic, "space opera");
  EXPECT_EQ(first.snapshot->metadata().style, "formal");
  EXPECT_EQ(first.snapshot->metadata().preference, "project names first");
  EXPECT_EQ(first.snapshot->metadata().payload_sha256.size(), 64);

  // Provenance-only rewrites intentionally retain the semantic sequence and
  // therefore the already indexed immutable native object.
  reader->set_project(
      Project("2026-07-17T00:00:01.000Z", "刹那"));
  const dictionary::ProjectDictionaryPublication timestamp_only =
      provider.Reload();
  EXPECT_EQ(timestamp_only.snapshot, first.snapshot);

  reader->set_project(
      Project("2026-07-17T00:00:02.000Z", "刹那改"));
  const dictionary::ProjectDictionaryPublication changed = provider.Reload();
  ASSERT_NE(changed.snapshot, nullptr);
  EXPECT_NE(changed.snapshot, first.snapshot);
  EXPECT_EQ(changed.snapshot->generation(), 2);

  reader->set_missing(true);
  const dictionary::ProjectDictionaryPublication grace = provider.Reload();
  EXPECT_EQ(grace.snapshot, changed.snapshot);
  EXPECT_FALSE(grace.clear);

  const dictionary::ProjectDictionaryPublication failed = provider.Reload();
  EXPECT_EQ(failed.snapshot, nullptr);
  EXPECT_TRUE(failed.clear);
}

TEST(ProjectDictionaryProviderTest, NullPublisherFailsClosed) {
  ProtocolV1ProjectDictionaryProvider provider(nullptr, kPosIds);
  const dictionary::ProjectDictionaryPublication result = provider.Reload();
  EXPECT_EQ(result.snapshot, nullptr);
  EXPECT_TRUE(result.clear);
}

}  // namespace
}  // namespace mozc::grimodex
