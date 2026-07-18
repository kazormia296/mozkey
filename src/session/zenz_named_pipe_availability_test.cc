// Copyright 2026 The Mozkey Authors

#include "session/zenz_named_pipe_client.h"

#include "testing/gunit.h"

namespace mozc {
namespace session {
namespace {

TEST(ZenzNamedPipeAvailabilityTest, MatchesPackagedRuntimeAvailability) {
  ZenzNamedPipeClient client;
  EXPECT_TRUE(client.IsAvailable());
}

#if defined(__APPLE__)
TEST(ZenzNamedPipeAvailabilityTest,
     MacosRejectsInvalidPipeBeforeAutoLaunch) {
  ZenzNamedPipeClient client;
  ZenzLiveRequest request;
  request.generation = 42;
  request.key = "key-retained-for-stale-response-filtering";
  request.pipe_name = "";
  request.timeout_msec = 60'000;

  const ZenzLiveResponse response = client.Convert(request);

  EXPECT_FALSE(response.ok);
  EXPECT_EQ(response.generation, request.generation);
  EXPECT_EQ(response.key, request.key);
  EXPECT_EQ(response.debug, "invalid_pipe_name");
}
#endif

}  // namespace
}  // namespace session
}  // namespace mozc
