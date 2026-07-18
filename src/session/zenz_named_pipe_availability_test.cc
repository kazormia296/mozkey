// Copyright 2026 The Mozkey Authors

#include "session/zenz_named_pipe_client.h"

#include "testing/gunit.h"

namespace mozc {
namespace session {
namespace {

TEST(ZenzNamedPipeAvailabilityTest, MatchesPackagedRuntimeAvailability) {
  ZenzNamedPipeClient client;

#if defined(__APPLE__)
  EXPECT_FALSE(client.IsAvailable());
#else
  EXPECT_TRUE(client.IsAvailable());
#endif
}

#if defined(__APPLE__)
TEST(ZenzNamedPipeAvailabilityTest,
     MacosFailsBeforePipeResolutionOrAutoLaunch) {
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
  EXPECT_EQ(response.debug, "macos_runtime_not_packaged");
}
#endif

}  // namespace
}  // namespace session
}  // namespace mozc
