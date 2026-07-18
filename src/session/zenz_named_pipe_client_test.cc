#include "session/zenz_named_pipe_client.h"

#if defined(__linux__)
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>
#endif

#include <string>
#include <thread>

#include "protocol/config.pb.h"
#include "session/zenz_named_pipe_endpoint.h"
#include "testing/gunit.h"

namespace mozc {
namespace session {
namespace {

TEST(ZenzNamedPipeClientTest, DefaultPipeMatchesConfigContract) {
  constexpr char kExpectedPipeName[] = R"(\\.\pipe\mozc_zenz_scorer)";

  config::Config config;
  EXPECT_STREQ(kDefaultZenzNamedPipeName, kExpectedPipeName);
  EXPECT_EQ(config.zenz_live_correction_pipe_name(), kExpectedPipeName);
}

TEST(ZenzNamedPipeClientTest, LinuxFallback) {
#if defined(__linux__)
  // Create a temporary directory to act as HOME
  char temp_dir[] = "/tmp/zenz_test_XXXXXX";
  EXPECT_TRUE(mkdtemp(temp_dir) != nullptr);

  setenv("HOME", temp_dir, 1);

  std::string socket_path = std::string(temp_dir) +
#if defined(__linux__) && !defined(GOOGLE_JAPANESE_INPUT_BUILD)
                            "/.mozkey_zenz_scorer_pipe";
#else
                            "/.mozc_zenz_scorer_pipe";
#endif

  // Create a dummy Unix domain socket
  int fd = socket(AF_UNIX, SOCK_STREAM, 0);
  EXPECT_GE(fd, 0);

  struct sockaddr_un addr;
  memset(&addr, 0, sizeof(addr));
  addr.sun_family = AF_UNIX;
  strncpy(addr.sun_path, socket_path.c_str(), sizeof(addr.sun_path) - 1);

  EXPECT_EQ(bind(fd, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)), 0);
  EXPECT_EQ(listen(fd, 1), 0);

  std::thread server_thread([fd]() {
    int client_fd = accept(fd, nullptr, nullptr);
    if (client_fd >= 0) {
      close(client_fd);
    }
  });

  ZenzNamedPipeClient client;
  ZenzLiveRequest request;
  // Windows-style named pipe path
  request.pipe_name = kDefaultZenzNamedPipeName;
  request.timeout_msec = 100;

  // The OSS Linux client uses Mozkey's product-specific fallback socket.
  ZenzLiveResponse response = client.Convert(request);

  server_thread.join();

  // Since our dummy socket closes immediately, the client will fail at write or read,
  // but crucially it should NOT fail with "pipe_open_failed" or "invalid_pipe_name".
  EXPECT_NE(response.debug, "pipe_open_failed");
  EXPECT_NE(response.debug, "invalid_pipe_name");

  close(fd);
  unlink(socket_path.c_str());
  rmdir(temp_dir);
#endif
}

}  // namespace
}  // namespace session
}  // namespace mozc
