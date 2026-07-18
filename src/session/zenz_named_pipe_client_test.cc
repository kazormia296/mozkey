#include "session/zenz_named_pipe_client.h"

#if !defined(_WIN32)
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>
#endif

#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <thread>
#include <utility>

#include "protocol/config.pb.h"
#include "session/zenz_named_pipe_endpoint.h"
#include "testing/gunit.h"

namespace mozc {
namespace session {
namespace {

#if !defined(_WIN32)
constexpr uint32_t kTestZenzWireMagic = 0x315A4E5A;
constexpr uint16_t kTestZenzWireVersion = 2;
constexpr uint16_t kTestZenzWireKindResponse = 2;
constexpr uint32_t kTestMaxWirePayloadBytes = 64 * 1024;

#pragma pack(push, 1)
struct TestZenzWireResponseHeader {
  uint32_t magic;
  uint16_t version;
  uint16_t kind;
  uint32_t generation;
  uint32_t status;
  uint32_t latency_msec;
  uint32_t value_size;
  uint32_t debug_size;
};
#pragma pack(pop)

ZenzLiveResponse ConvertWithResponsePayloadSizes(uint32_t value_size,
                                                 uint32_t debug_size) {
  char temp_dir[] = "/tmp/zenz_payload_test_XXXXXX";
  EXPECT_NE(mkdtemp(temp_dir), nullptr);
  const std::string socket_path = std::string(temp_dir) + "/runtime.sock";

  const int server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
  EXPECT_GE(server_fd, 0);

  struct sockaddr_un addr = {};
  addr.sun_family = AF_UNIX;
  strncpy(addr.sun_path, socket_path.c_str(), sizeof(addr.sun_path) - 1);
  const socklen_t addr_size = static_cast<socklen_t>(
      offsetof(struct sockaddr_un, sun_path) + socket_path.size() + 1);
#if defined(__APPLE__)
  addr.sun_len = static_cast<unsigned char>(addr_size);
#endif
  EXPECT_EQ(bind(server_fd, reinterpret_cast<struct sockaddr *>(&addr),
                 addr_size),
            0);
  EXPECT_EQ(chmod(socket_path.c_str(), 0600), 0);
  EXPECT_EQ(listen(server_fd, 1), 0);

  constexpr uint32_t kGeneration = 73;
  std::thread server_thread([server_fd, value_size, debug_size]() {
    const int client_fd = accept(server_fd, nullptr, nullptr);
    if (client_fd < 0) {
      return;
    }
    const TestZenzWireResponseHeader header = {
        .magic = kTestZenzWireMagic,
        .version = kTestZenzWireVersion,
        .kind = kTestZenzWireKindResponse,
        .generation = kGeneration,
        .status = 0,
        .latency_msec = 1,
        .value_size = value_size,
        .debug_size = debug_size,
    };
#if defined(__APPLE__)
    constexpr int kSendFlags = 0;
#else
    constexpr int kSendFlags = MSG_NOSIGNAL;
#endif
    send(client_fd, &header, sizeof(header), kSendFlags);
    close(client_fd);
  });

  ZenzNamedPipeClient client;
  ZenzLiveRequest request;
  request.generation = kGeneration;
  request.pipe_name = socket_path;
  request.timeout_msec = 100;
  const ZenzLiveResponse response = client.Convert(request);

  server_thread.join();
  close(server_fd);
  unlink(socket_path.c_str());
  rmdir(temp_dir);
  return response;
}
#endif  // !defined(_WIN32)

TEST(ZenzNamedPipeClientTest, DefaultPipeMatchesConfigContract) {
  constexpr char kExpectedPipeName[] = R"(\\.\pipe\mozc_zenz_scorer)";

  config::Config config;
  EXPECT_STREQ(kDefaultZenzNamedPipeName, kExpectedPipeName);
  EXPECT_EQ(config.zenz_live_correction_pipe_name(), kExpectedPipeName);
}

TEST(ZenzNamedPipeClientTest, RejectsOversizedBackendDeviceBeforeConnecting) {
  ZenzNamedPipeClient client;
  ZenzLiveRequest request;
  request.backend_device = std::string(129, 'x');

  const ZenzLiveResponse response = client.Convert(request);

  EXPECT_FALSE(response.ok);
  EXPECT_EQ(response.debug, "backend_device_too_large");
}

TEST(ZenzNamedPipeClientTest, UnixFallback) {
#if !defined(_WIN32)
  // Create a temporary directory to act as HOME
  char temp_dir[] = "/tmp/zenz_test_XXXXXX";
  EXPECT_TRUE(mkdtemp(temp_dir) != nullptr);

  const char* original_home = getenv("HOME");
  const bool had_original_home = original_home != nullptr;
  const std::string saved_home = original_home ? original_home : "";
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
  const socklen_t addr_size = static_cast<socklen_t>(
      offsetof(struct sockaddr_un, sun_path) + socket_path.size() + 1);
#if defined(__APPLE__)
  addr.sun_len = static_cast<unsigned char>(addr_size);
#endif

  EXPECT_EQ(bind(fd, reinterpret_cast<struct sockaddr*>(&addr), addr_size), 0);
  EXPECT_EQ(chmod(socket_path.c_str(), 0600), 0);
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
  if (had_original_home) {
    setenv("HOME", saved_home.c_str(), 1);
  } else {
    unsetenv("HOME");
  }
#endif
}

TEST(ZenzNamedPipeClientTest, RejectsOversizedResponseBeforeAllocation) {
#if !defined(_WIN32)
  for (const auto &[value_size, debug_size] : {
           std::pair<uint32_t, uint32_t>{
               std::numeric_limits<uint32_t>::max(), 0},
           std::pair<uint32_t, uint32_t>{
               0, std::numeric_limits<uint32_t>::max()},
           std::pair<uint32_t, uint32_t>{
               kTestMaxWirePayloadBytes, 1},
       }) {
    SCOPED_TRACE(testing::Message()
                 << "value_size=" << value_size
                 << " debug_size=" << debug_size);
    const ZenzLiveResponse response =
        ConvertWithResponsePayloadSizes(value_size, debug_size);
    EXPECT_FALSE(response.ok);
    EXPECT_EQ(response.debug, "pipe_response_payload_too_large");
  }
#endif
}

}  // namespace
}  // namespace session
}  // namespace mozc
