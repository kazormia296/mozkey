// Copyright 2026 The Mozkey Authors
// Licensed under the Apache License, Version 2.0.

// Linux real-process fault injection for the Grimodex/Fcitx session contract.
// Unit tests cover policy details; this test deliberately crosses the actual
// mozc_server Unix socket and process boundary.

#include <fcntl.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

#include <atomic>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "absl/strings/str_cat.h"
#include "absl/time/clock.h"
#include "absl/time/time.h"
#include "base/process.h"
#include "base/system_util.h"
#include "client/client.h"
#include "client/client_interface.h"
#include "grimodex/protocol_v1.h"
#include "ipc/ipc.h"
#include "ipc/ipc_path_manager.h"
#include "protocol/commands.pb.h"
#include "protocol/config.pb.h"
#include "testing/gunit.h"
#include "testing/mozctest.h"
#include "unix/fcitx5/grimodex_context.h"
#include "unix/fcitx5/mozc_client_interface.h"
#include "unix/fcitx5/raw_reading_recovery.h"

namespace mozc::unix_runtime_test {
namespace {

constexpr char kStateFile[] = "state.json";
constexpr char kProjectFile[] = "project-a.json";

class ScopedEnvironment final {
 public:
  ScopedEnvironment() = default;
  ScopedEnvironment(const ScopedEnvironment&) = delete;
  ScopedEnvironment& operator=(const ScopedEnvironment&) = delete;

  ~ScopedEnvironment() {
    for (auto iter = saved_.rbegin(); iter != saved_.rend(); ++iter) {
      if (iter->second.has_value()) {
        setenv(iter->first.c_str(), iter->second->c_str(), 1);
      } else {
        unsetenv(iter->first.c_str());
      }
    }
  }

  void Set(std::string name, const std::string& value) {
    const char* const old = getenv(name.c_str());
    saved_.emplace_back(name, old == nullptr
                                  ? std::nullopt
                                  : std::optional<std::string>(old));
    ASSERT_EQ(setenv(name.c_str(), value.c_str(), 1), 0);
  }

 private:
  std::vector<std::pair<std::string, std::optional<std::string>>> saved_;
};

bool MakeDirectory(const std::string& path) {
  return mkdir(path.c_str(), 0700) == 0 && chmod(path.c_str(), 0700) == 0;
}

bool AtomicReplace(const std::string& directory, std::string_view filename,
                   std::string_view bytes, uint64_t serial) {
  const std::string temporary =
      absl::StrCat(directory, "/.", filename, ".tmp-", getpid(), "-", serial);
  const std::string destination = absl::StrCat(directory, "/", filename);
  const int fd = open(temporary.c_str(),
                      O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  if (fd < 0) {
    return false;
  }
  size_t offset = 0;
  while (offset < bytes.size()) {
    const ssize_t written =
        write(fd, bytes.data() + offset, bytes.size() - offset);
    if (written <= 0) {
      close(fd);
      unlink(temporary.c_str());
      return false;
    }
    offset += static_cast<size_t>(written);
  }
  const bool synced = fsync(fd) == 0;
  const bool closed = close(fd) == 0;
  if (!synced || !closed ||
      rename(temporary.c_str(), destination.c_str()) != 0) {
    unlink(temporary.c_str());
    return false;
  }
  const int directory_fd =
      open(directory.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (directory_fd < 0) {
    return false;
  }
  const bool directory_synced = fsync(directory_fd) == 0;
  close(directory_fd);
  return directory_synced;
}

struct FileSnapshot final {
  bool readable = false;
  bool exists = false;
  std::string bytes;

  bool operator==(const FileSnapshot&) const = default;
};

FileSnapshot SnapshotFile(const std::string& path) {
  FileSnapshot snapshot;
  const int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
  if (fd < 0) {
    snapshot.readable = errno == ENOENT;
    return snapshot;
  }
  snapshot.exists = true;
  char buffer[4096];
  while (true) {
    const ssize_t size = read(fd, buffer, sizeof(buffer));
    if (size > 0) {
      snapshot.bytes.append(buffer, static_cast<size_t>(size));
      continue;
    }
    if (size < 0 && errno == EINTR) {
      continue;
    }
    snapshot.readable = size == 0;
    break;
  }
  close(fd);
  return snapshot;
}

class FakeZenzEndpoint final {
 public:
  FakeZenzEndpoint() = default;
  FakeZenzEndpoint(const FakeZenzEndpoint&) = delete;
  FakeZenzEndpoint& operator=(const FakeZenzEndpoint&) = delete;

  ~FakeZenzEndpoint() {
    if (fd_ >= 0) {
      close(fd_);
    }
    if (!path_.empty()) {
      unlink(path_.c_str());
    }
  }

  bool Open(const std::string& path) {
    if (fd_ >= 0 || path.empty() ||
        path.size() >= sizeof(sockaddr_un::sun_path)) {
      return false;
    }
    path_ = path;
    fd_ = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd_ < 0) {
      return false;
    }
    sockaddr_un address = {};
    address.sun_family = AF_UNIX;
    memcpy(address.sun_path, path.data(), path.size());
    const socklen_t address_length = static_cast<socklen_t>(
        offsetof(sockaddr_un, sun_path) + path.size() + 1);
    if (bind(fd_, reinterpret_cast<sockaddr*>(&address), address_length) != 0 ||
        listen(fd_, 8) != 0) {
      return false;
    }
    const int flags = fcntl(fd_, F_GETFL, 0);
    return flags >= 0 && fcntl(fd_, F_SETFL, flags | O_NONBLOCK) == 0;
  }

  // A secure session must not even attempt to open the configured scorer.
  // Returning a count instead of serving responses makes a policy regression
  // visible without introducing another asynchronous test component.
  int DrainAcceptedConnections() {
    int accepted_count = 0;
    while (true) {
      const int accepted =
          accept4(fd_, nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
      if (accepted >= 0) {
        ++accepted_count;
        close(accepted);
        continue;
      }
      if (errno == EINTR) {
        continue;
      }
      return (errno == EAGAIN || errno == EWOULDBLOCK) ? accepted_count : -1;
    }
  }

 private:
  std::string path_;
  int fd_ = -1;
};

std::string StateJson(std::string_view timestamp) {
  return absl::StrCat(R"json({
  "format_version": 1,
  "active_project_id": "project-a",
  "updated_at": ")json",
                      timestamp, R"json("
})json");
}

std::string ProjectJson(std::string_view surface,
                        std::string_view timestamp) {
  return absl::StrCat(R"json({
  "format_version": 1,
  "project_id": "project-a",
  "project_name": "runtime failure test",
  "generated_at": ")json",
                      timestamp, R"json(",
  "entries": [{
    "yomi": "せつな",
    "surface": ")json",
                      surface, R"json(",
    "category": "person",
    "priority": 3,
    "entry_id": "entry-setsuna"
  }]
})json");
}

std::string Hex(std::string_view bytes) {
  constexpr char kHex[] = "0123456789abcdef";
  std::string result;
  result.reserve(bytes.size() * 2);
  for (const unsigned char byte : bytes) {
    result.push_back(kHex[byte >> 4]);
    result.push_back(kHex[byte & 0x0F]);
  }
  return result;
}

std::string PreeditValue(const commands::Output& output) {
  std::string value;
  if (!output.has_preedit()) {
    return value;
  }
  for (const commands::Preedit::Segment& segment :
       output.preedit().segment()) {
    value.append(segment.value());
  }
  return value;
}

bool CandidateWindowContains(const commands::CandidateWindow& window,
                             std::string_view value) {
  for (const commands::CandidateWindow::Candidate& candidate :
       window.candidate()) {
    if (candidate.value() == value) {
      return true;
    }
  }
  return window.has_sub_candidate_window() &&
         CandidateWindowContains(window.sub_candidate_window(), value);
}

commands::KeyEvent Character(char value) {
  commands::KeyEvent key;
  key.set_key_code(static_cast<uint32_t>(value));
  return key;
}

commands::KeyEvent Text(std::string_view value) {
  commands::KeyEvent key;
  key.set_key_string(value);
  return key;
}

struct StatusTuple final {
  bool present = false;
  commands::GrimodexSessionStatus::Scope scope =
      commands::GrimodexSessionStatus::OFF;
  uint64_t sequence = 0;
  std::string digest;

  bool operator==(const StatusTuple&) const = default;
};

StatusTuple ProjectStatus(const commands::Output& output) {
  if (!output.has_grimodex_session_status()) {
    return {};
  }
  const commands::GrimodexSessionStatus& status =
      output.grimodex_session_status();
  return StatusTuple{
      .present = true,
      .scope = status.scope(),
      .sequence = status.registry_sequence(),
      .digest = Hex(status.pinned_payload_sha256()),
  };
}

void ExpectSecureSuppression(const commands::Output& output) {
  const StatusTuple status = ProjectStatus(output);
  EXPECT_TRUE(status.present);
  EXPECT_EQ(status.scope, commands::GrimodexSessionStatus::SECURE_REVOKED);
  EXPECT_EQ(status.sequence, 0);
  EXPECT_TRUE(status.digest.empty());
  EXPECT_FALSE(output.has_candidate_window());
  EXPECT_FALSE(output.has_callback());
  EXPECT_FALSE(output.live_conversion());
  EXPECT_FALSE(output.live_conversion_pending());
  EXPECT_FALSE(output.zenz_live_correction_pending());
  EXPECT_FALSE(output.zenz_live_correction_applied());
}

class ServerProcessState final {
 public:
  explicit ServerProcessState(std::string server_path)
      : server_path_(std::move(server_path)) {}

  const std::string& server_path() const { return server_path_; }

  bool Start(client::ClientInterface* client) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (client->PingServer()) {
      return true;
    }
    ReapOrKillLocked();
    size_t pid = 0;
    if (!Process::SpawnProcess(server_path_, "", &pid) || pid == 0) {
      return false;
    }
    pid_ = static_cast<pid_t>(pid);
    for (int attempt = 0; attempt < 400; ++attempt) {
      if (client->PingServer()) {
        return true;
      }
      absl::SleepFor(absl::Milliseconds(25));
    }
    ReapOrKillLocked();
    return false;
  }

  bool KillActive() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (pid_ <= 0) {
      return true;
    }
    const pid_t victim = pid_;
    if (kill(victim, SIGKILL) != 0 && errno != ESRCH) {
      return false;
    }
    int status = 0;
    while (waitpid(victim, &status, 0) < 0 && errno == EINTR) {
    }
    pid_ = 0;
    return true;
  }

 private:
  void ReapOrKillLocked() {
    if (pid_ <= 0) {
      return;
    }
    int status = 0;
    const pid_t result = waitpid(pid_, &status, WNOHANG);
    if (result == 0) {
      kill(pid_, SIGKILL);
      while (waitpid(pid_, &status, 0) < 0 && errno == EINTR) {
      }
    }
    pid_ = 0;
  }

  const std::string server_path_;
  std::mutex mutex_;
  pid_t pid_ = 0;
};

class RuntimeServerLauncher final : public client::ServerLauncherInterface {
 public:
  explicit RuntimeServerLauncher(std::shared_ptr<ServerProcessState> state)
      : state_(std::move(state)) {}

  bool StartServer(client::ClientInterface* client) override {
    return state_->Start(client);
  }
  bool ForceTerminateServer(absl::string_view) override {
    return state_->KillActive();
  }
  bool WaitServer(uint32_t) override { return true; }
  void OnFatal(ServerErrorType) override {}
  std::string server_program() const override { return state_->server_path(); }
  void set_suppress_error_dialog(bool) override {}

 private:
  std::shared_ptr<ServerProcessState> state_;
};

class RuntimeFcitxClient final : public fcitx::MozcClientInterface {
 public:
  explicit RuntimeFcitxClient(std::shared_ptr<ServerProcessState> state) {
    client_.SetServerLauncher(
        std::make_unique<RuntimeServerLauncher>(std::move(state)));
    client_.set_suppress_error_dialog(true);
  }

  bool EnsureConnection() override { return client_.EnsureConnection(); }
  bool EnsureSession() override { return client_.EnsureSession(); }
  uint64_t session_generation() const override {
    return client_.session_generation();
  }
  bool SendKeyWithContext(const commands::KeyEvent& key,
                          const commands::Context& context,
                          commands::Output* output) override {
    return client_.SendKeyWithContext(key, context, output);
  }
  bool SendCommandWithContext(const commands::SessionCommand& command,
                              const commands::Context& context,
                              commands::Output* output) override {
    return client_.SendCommandWithContext(command, context, output);
  }
  bool IsDirectModeCommand(const commands::KeyEvent& key) const override {
    return client_.IsDirectModeCommand(key);
  }
  bool GetConfig(config::Config* config) override {
    return client_.GetConfig(config);
  }
  void set_client_capability(const commands::Capability& capability) override {
    client_.set_client_capability(capability);
  }
  bool SyncData() override { return client_.SyncData(); }
  bool LaunchTool(const std::string&, std::string_view) override {
    return false;
  }
  bool LaunchToolWithProtoBuf(const commands::Output&) override {
    return false;
  }

  bool PingServerForTest() const { return client_.PingServer(); }
  bool SetConfigForTest(const config::Config& config) {
    return client_.SetConfig(config);
  }

 private:
  client::Client client_;
};

bool Activate(RuntimeFcitxClient* client,
              const commands::Context& context) {
  if (!client->EnsureSession()) {
    return false;
  }
  commands::SessionCommand command;
  command.set_type(commands::SessionCommand::SWITCH_COMPOSITION_MODE);
  command.set_composition_mode(commands::HIRAGANA);
  commands::Output output;
  return fcitx::SendCommandWithGrimodexContext(client, command, context,
                                                &output);
}

fcitx::RawReadingRecovery MakeRuntimeRecovery() {
  return fcitx::RawReadingRecovery(
      [](fcitx::MozcClientInterface* client, uint64_t generation,
         const commands::Context& context) {
        if (client == nullptr || generation == 0 ||
            client->session_generation() != generation) {
          return false;
        }
        commands::SessionCommand command;
        command.set_type(commands::SessionCommand::SWITCH_COMPOSITION_MODE);
        command.set_composition_mode(commands::HIRAGANA);
        commands::Output output;
        return fcitx::SendCommandWithGrimodexContext(
                   client, command, context, &output) &&
               client->session_generation() == generation &&
               output.has_mode();
      });
}

bool SendMalformedPartialRequest() {
  IPCPathManager* const manager =
      IPCPathManager::GetIPCPathManager("session");
  if (manager == nullptr) {
    return false;
  }
  std::string server_address;
  if (!manager->GetPathName(&server_address) ||
      server_address.size() >= sizeof(sockaddr_un::sun_path)) {
    return false;
  }
  const int socket_fd = socket(PF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (socket_fd < 0) {
    return false;
  }
  sockaddr_un address = {};
  address.sun_family = AF_UNIX;
  memcpy(address.sun_path, server_address.data(), server_address.size());
  const socklen_t address_length =
      static_cast<socklen_t>(sizeof(address.sun_family) +
                             server_address.size());
  if (connect(socket_fd, reinterpret_cast<sockaddr*>(&address),
              address_length) != 0) {
    close(socket_fd);
    return false;
  }
  // An unterminated protobuf varint is a deterministic truncated frame.
  const unsigned char partial = 0x80;
  const bool sent = send(socket_fd, &partial, 1, MSG_NOSIGNAL) == 1;
  const bool shut_down = shutdown(socket_fd, SHUT_WR) == 0;
  timeval timeout = {.tv_sec = 2, .tv_usec = 0};
  const bool timeout_set =
      setsockopt(socket_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout,
                 sizeof(timeout)) == 0;
  bool response_closed = false;
  char response[16];
  while (timeout_set) {
    const ssize_t received = recv(socket_fd, response, sizeof(response), 0);
    if (received == 0) {
      response_closed = true;
      break;
    }
    if (received < 0 && errno != EINTR) {
      break;
    }
  }
  close(socket_fd);
  return sent && shut_down && timeout_set && response_closed;
}

class GrimodexRuntimeFailureTest : public ::testing::Test {
 protected:
  GrimodexRuntimeFailureTest() : temp_(testing::MakeTempDirectoryOrDie()) {}

  void SetUp() override {
    home_ = absl::StrCat(temp_.path(), "/home");
    xdg_config_ = absl::StrCat(temp_.path(), "/config");
    profile_ = absl::StrCat(xdg_config_, "/mozkey-ibg");
    protocol_root_ = absl::StrCat(temp_.path(), "/ime");
    projects_ = absl::StrCat(protocol_root_, "/projects");
    ASSERT_TRUE(MakeDirectory(home_));
    ASSERT_TRUE(MakeDirectory(xdg_config_));
    ASSERT_TRUE(MakeDirectory(profile_));
    ASSERT_TRUE(MakeDirectory(protocol_root_));
    ASSERT_TRUE(MakeDirectory(projects_));

    environment_ = std::make_unique<ScopedEnvironment>();
    environment_->Set("HOME", home_);
    environment_->Set("XDG_CONFIG_HOME", xdg_config_);
    environment_->Set("GRIMODEX_IME_ROOT", protocol_root_);
    old_profile_ = SystemUtil::GetUserProfileDirectory();
    SystemUtil::SetUserProfileDirectory(profile_);

    project_a_ = ProjectJson("刹那A", "2026-07-17T00:00:00.000Z");
    project_b_ = ProjectJson("刹那B", "2026-07-17T00:00:01.000Z");
    digest_a_ = grimodex::VerifiedFileBytes::FromBytes(project_a_).sha256;
    digest_b_ = grimodex::VerifiedFileBytes::FromBytes(project_b_).sha256;
    ASSERT_TRUE(Publish(project_a_, "2026-07-17T00:00:00.000Z"));

    const std::string runfile_server =
        testing::GetSourceFileOrDie({"server", "mozc_server"});
    char* const canonical_server = realpath(runfile_server.c_str(), nullptr);
    ASSERT_NE(canonical_server, nullptr) << strerror(errno);
    state_ = std::make_shared<ServerProcessState>(canonical_server);
    free(canonical_server);
  }

  void TearDown() override {
    if (state_ != nullptr) {
      EXPECT_TRUE(state_->KillActive());
    }
    SystemUtil::SetUserProfileDirectory(old_profile_);
    environment_.reset();
  }

  bool Publish(std::string_view project, std::string_view timestamp) {
    const uint64_t project_serial = serial_.fetch_add(1);
    const uint64_t state_serial = serial_.fetch_add(1);
    return AtomicReplace(projects_, kProjectFile, project, project_serial) &&
           AtomicReplace(protocol_root_, kStateFile, StateJson(timestamp),
                         state_serial);
  }

  TempDirectory temp_;
  std::unique_ptr<ScopedEnvironment> environment_;
  std::shared_ptr<ServerProcessState> state_;
  std::atomic<uint64_t> serial_ = 1;
  std::string old_profile_;
  std::string home_;
  std::string xdg_config_;
  std::string profile_;
  std::string protocol_root_;
  std::string projects_;
  std::string project_a_;
  std::string project_b_;
  std::string digest_a_;
  std::string digest_b_;
};

TEST_F(GrimodexRuntimeFailureTest,
       KillRestartPartialIpcSnapshotRaceAndSessionIsolation) {
  const commands::Context first_context = fcitx::BuildGrimodexContext(
      "grimodex", "wayland-first", false, 1);
  const commands::Context second_context = fcitx::BuildGrimodexContext(
      "com.miyakey.grimodex", "wayland-second", false, 1);

  RuntimeFcitxClient first_client(state_);
  RuntimeFcitxClient second_client(state_);
  ASSERT_TRUE(Activate(&first_client, first_context));
  ASSERT_TRUE(Activate(&second_client, second_context));

  fcitx::RawReadingRecovery first_recovery = MakeRuntimeRecovery();
  fcitx::RawReadingRecovery second_recovery = MakeRuntimeRecovery();
  commands::Output first_output;
  commands::Output second_output;
  ASSERT_TRUE(first_recovery.DispatchKey(&first_client, Character('a'),
                                         first_context, false, &first_output));
  first_recovery.RecordSuccessfulKey(Character('a'), first_output, false);
  ASSERT_TRUE(second_recovery.DispatchKey(&second_client, Character('k'),
                                          second_context, false,
                                          &second_output));
  second_recovery.RecordSuccessfulKey(Character('k'), second_output, false);
  ASSERT_EQ(first_recovery.journal_size_for_test(), 1);
  ASSERT_EQ(second_recovery.journal_size_for_test(), 1);

  ASSERT_TRUE(SendMalformedPartialRequest());
  ASSERT_TRUE(first_client.PingServerForTest());

  const uint64_t first_generation = first_client.session_generation();
  const uint64_t second_generation = second_client.session_generation();
  ASSERT_TRUE(state_->KillActive());

  ASSERT_TRUE(first_recovery.DispatchKey(&first_client, Character('i'),
                                         first_context, false, &first_output));
  first_recovery.RecordSuccessfulKey(Character('i'), first_output, false);
  ASSERT_TRUE(second_recovery.DispatchKey(&second_client, Character('u'),
                                          second_context, false,
                                          &second_output));
  second_recovery.RecordSuccessfulKey(Character('u'), second_output, false);
  EXPECT_GT(first_client.session_generation(), first_generation);
  EXPECT_GT(second_client.session_generation(), second_generation);
  EXPECT_EQ(PreeditValue(first_output), "あい");
  EXPECT_EQ(PreeditValue(second_output), "く");

  // Conversion/action keys suppress the raw journal.  A candidate command
  // constructed in the old session is then presented after SIGKILL; the
  // managed client must create a replacement session but not issue it there.
  commands::KeyEvent space;
  space.set_special_key(commands::KeyEvent::SPACE);
  ASSERT_TRUE(first_recovery.DispatchKey(&first_client, space, first_context,
                                         false, &first_output));
  first_recovery.RecordSuccessfulKey(space, first_output, false);
  ASSERT_TRUE(first_recovery.DispatchKey(&first_client, space, first_context,
                                         false, &first_output));
  first_recovery.RecordSuccessfulKey(space, first_output, false);
  ASSERT_TRUE(first_recovery.journal_suppressed_for_test());
  ASSERT_TRUE(first_output.has_candidate_window());
  ASSERT_GT(first_output.candidate_window().candidate_size(), 0);
  ASSERT_TRUE(first_output.candidate_window().candidate(0).has_id());
  commands::SessionCommand stale_candidate;
  stale_candidate.set_type(commands::SessionCommand::SELECT_CANDIDATE);
  stale_candidate.set_id(first_output.candidate_window().candidate(0).id());
  const uint64_t candidate_generation = first_client.session_generation();
  ASSERT_TRUE(state_->KillActive());
  commands::Output stale_output;
  EXPECT_FALSE(fcitx::SendCommandWithGrimodexContext(
      &first_client, stale_candidate, first_context, &stale_output));
  EXPECT_GT(first_client.session_generation(), candidate_generation);
  ASSERT_TRUE(first_recovery.DispatchKey(&first_client, Character('u'),
                                         first_context, false, &first_output));
  EXPECT_FALSE(first_output.has_result());
  EXPECT_EQ(PreeditValue(first_output), "う");

  // Exercise the capability boundary on one real server session.  The normal
  // side first owns a project snapshot and a raw journal.  Moving that same
  // session into secure input must purge both before processing the new key.
  RuntimeFcitxClient secure_client(state_);
  fcitx::RawReadingRecovery secure_recovery = MakeRuntimeRecovery();
  const commands::Context before_secure_context =
      fcitx::BuildGrimodexContext(" GrImOdEx ", "wayland", false, 1);
  ASSERT_TRUE(Activate(&secure_client, before_secure_context));
  commands::Output secure_output;
  ASSERT_TRUE(secure_recovery.DispatchKey(&secure_client, Character('p'),
                                          before_secure_context, false,
                                          &secure_output));
  secure_recovery.RecordSuccessfulKey(Character('p'), secure_output, false);
  EXPECT_EQ(secure_recovery.journal_size_for_test(), 1);
  EXPECT_EQ(ProjectStatus(secure_output).scope,
            commands::GrimodexSessionStatus::PROJECT);
  EXPECT_EQ(ProjectStatus(secure_output).digest, digest_a_);
  const std::string normal_preedit = PreeditValue(secure_output);
  ASSERT_FALSE(normal_preedit.empty());

  // Turn every optional conversion path on and point Zenz at an observable
  // fake endpoint.  The secure policy must dominate this configuration.
  config::Config original_config;
  ASSERT_TRUE(secure_client.GetConfig(&original_config));
  config::Config aggressive_config = original_config;
  aggressive_config.set_use_live_conversion(true);
  aggressive_config.set_live_conversion_delay_msec(0);
  aggressive_config.set_live_conversion_min_key_length(1);
  aggressive_config.set_use_zenz_live_correction(true);
  aggressive_config.set_zenz_live_correction_delay_msec(0);
  aggressive_config.set_zenz_live_correction_timeout_msec(50);
  aggressive_config.set_zenz_live_correction_min_key_length(1);
  aggressive_config.set_use_zenz_synthetic_candidate(true);
  aggressive_config.set_use_zenz_feedback_learning(true);
  // Bazel's TEST_TMPDIR can exceed sockaddr_un::sun_path.  Keep only this
  // process-owned endpoint in the system temp directory.
  const std::string zenz_endpoint_path =
      absl::StrCat("/tmp/mozkey-zenz-secure-", getpid(), ".sock");
  aggressive_config.set_zenz_live_correction_pipe_name(zenz_endpoint_path);
  ASSERT_TRUE(secure_client.SetConfigForTest(aggressive_config));
  ASSERT_TRUE(secure_client.SyncData());

  const std::string history_path = absl::StrCat(profile_, "/.history.db");
  const std::string feedback_path =
      absl::StrCat(profile_, "/zenz_feedback.tsv");
  const FileSnapshot history_before = SnapshotFile(history_path);
  const FileSnapshot feedback_before = SnapshotFile(feedback_path);
  ASSERT_TRUE(history_before.readable);
  ASSERT_TRUE(feedback_before.readable);

  FakeZenzEndpoint zenz_endpoint;
  ASSERT_TRUE(zenz_endpoint.Open(zenz_endpoint_path));
  int surrounding_text_calls = 0;
  const commands::Context secure_context = fcitx::BuildGrimodexContext(
      " GrImOdEx ", "wayland", true, 2, [&] {
        ++surrounding_text_calls;
        return std::optional<fcitx::GrimodexSurroundingText>(
            fcitx::GrimodexSurroundingText{
                .preceding_text = "must-not-cross-left",
                .following_text = "must-not-cross-right",
            });
      });
  EXPECT_EQ(surrounding_text_calls, 0);
  EXPECT_FALSE(secure_context.has_preceding_text());
  EXPECT_FALSE(secure_context.has_following_text());
  secure_recovery.ResetSessionBoundary();
  std::string secure_host_literal;
  const commands::KeyEvent secure_project_yomi = Text("せつな");
  ASSERT_TRUE(secure_recovery.DispatchKey(&secure_client, secure_project_yomi,
                                          secure_context, true,
                                          &secure_output));
  secure_recovery.RecordSuccessfulKey(secure_project_yomi, secure_output,
                                      true);
  if (secure_output.has_result()) {
    secure_host_literal.append(secure_output.result().value());
  }
  EXPECT_EQ(secure_recovery.journal_size_for_test(), 0);
  ExpectSecureSuppression(secure_output);
  EXPECT_EQ(PreeditValue(secure_output).find(normal_preedit), std::string::npos);

  // Use the exact project-dictionary yomi as one frontend text event.  Even
  // with live conversion and Zenz enabled, secure input may expose only that
  // literal text, never the project surface.
  const std::string secure_literal_tail = PreeditValue(secure_output);
  ASSERT_EQ(absl::StrCat(secure_host_literal, secure_literal_tail), "せつな");
  commands::SessionCommand secure_submit;
  secure_submit.set_type(commands::SessionCommand::SUBMIT);
  if (!secure_literal_tail.empty()) {
    // Password-mode composition may commit all but its last kana immediately.
    // A following literal text event flushes that tail; REVERT then discards
    // only the new uncommitted probe character.
    const commands::KeyEvent flush_secure_tail = Text("あ");
    commands::Output secure_flush_output;
    ASSERT_TRUE(secure_recovery.DispatchKey(
        &secure_client, flush_secure_tail, secure_context, true,
        &secure_flush_output));
    secure_recovery.RecordSuccessfulKey(flush_secure_tail,
                                        secure_flush_output, true);
    ExpectSecureSuppression(secure_flush_output);
    ASSERT_TRUE(secure_flush_output.has_result());
    EXPECT_EQ(secure_flush_output.result().value(), secure_literal_tail);
    secure_host_literal.append(secure_flush_output.result().value());

    commands::SessionCommand discard_probe;
    discard_probe.set_type(commands::SessionCommand::REVERT);
    commands::Output discarded_probe;
    ASSERT_TRUE(fcitx::SendCommandWithGrimodexContext(
        &secure_client, discard_probe, secure_context, &discarded_probe));
    ExpectSecureSuppression(discarded_probe);
  }
  EXPECT_EQ(secure_host_literal, "せつな");

  // Give a hypothetical asynchronous scorer request enough time to reach the
  // listening socket, then flush storage.  Neither Zenz nor either learning
  // store may observe the secure composition/commit.
  absl::SleepFor(absl::Milliseconds(100));
  EXPECT_EQ(zenz_endpoint.DrainAcceptedConnections(), 0);
  ASSERT_TRUE(secure_client.SyncData());
  const FileSnapshot history_after = SnapshotFile(history_path);
  const FileSnapshot feedback_after = SnapshotFile(feedback_path);
  ASSERT_TRUE(history_after.readable);
  ASSERT_TRUE(feedback_after.readable);
  EXPECT_EQ(history_after, history_before);
  EXPECT_EQ(feedback_after, feedback_before);

  // Secure input never retries the key that first discovers a dead process.
  // The following user key may create a fresh secure session, but the failed
  // key must not appear in its output and no scorer call may be scheduled.
  ASSERT_TRUE(secure_recovery.DispatchKey(&secure_client, Character('p'),
                                          secure_context, true,
                                          &secure_output));
  ExpectSecureSuppression(secure_output);
  const uint64_t secure_generation = secure_client.session_generation();
  ASSERT_TRUE(state_->KillActive());
  EXPECT_FALSE(secure_recovery.DispatchKey(&secure_client, Character('x'),
                                           secure_context, true,
                                           &secure_output));
  EXPECT_GT(secure_client.session_generation(), secure_generation);
  const uint64_t recreated_secure_generation =
      secure_client.session_generation();
  ASSERT_TRUE(secure_recovery.DispatchKey(&secure_client, Character('y'),
                                          secure_context, true,
                                          &secure_output));
  EXPECT_EQ(secure_client.session_generation(), recreated_secure_generation);
  ExpectSecureSuppression(secure_output);

  RuntimeFcitxClient secure_reference_client(state_);
  fcitx::RawReadingRecovery secure_reference_recovery =
      MakeRuntimeRecovery();
  ASSERT_TRUE(Activate(&secure_reference_client, secure_context));
  commands::Output secure_reference_output;
  ASSERT_TRUE(secure_reference_recovery.DispatchKey(
      &secure_reference_client, Character('y'), secure_context, true,
      &secure_reference_output));
  ExpectSecureSuppression(secure_reference_output);
  const std::string reference_y = PreeditValue(secure_reference_output);
  ASSERT_FALSE(reference_y.empty());
  EXPECT_EQ(PreeditValue(secure_output), reference_y);

  commands::Output post_failure_submit_output;
  ASSERT_TRUE(fcitx::SendCommandWithGrimodexContext(
      &secure_client, secure_submit, secure_context,
      &post_failure_submit_output));
  ExpectSecureSuppression(post_failure_submit_output);
  ASSERT_TRUE(post_failure_submit_output.has_result());
  EXPECT_EQ(post_failure_submit_output.result().value(), reference_y);
  absl::SleepFor(absl::Milliseconds(100));
  EXPECT_EQ(zenz_endpoint.DrainAcceptedConnections(), 0);
  ASSERT_TRUE(secure_client.SyncData());
  EXPECT_EQ(SnapshotFile(history_path), history_before);
  EXPECT_EQ(SnapshotFile(feedback_path), feedback_before);

  // A strictly newer non-secure epoch may unlock the session, but an unknown
  // application remains outside project scope.  Neither the old normal
  // preedit nor the secure preedit is allowed to survive the boundary.
  ASSERT_TRUE(secure_client.SetConfigForTest(original_config));
  const commands::Context after_secure_context = fcitx::BuildGrimodexContext(
      "org.example.Password", "wayland", false, 3, [] {
        return std::optional<fcitx::GrimodexSurroundingText>(
            fcitx::GrimodexSurroundingText{
                .preceding_text = "public-left",
                .following_text = "public-right",
            });
      });
  secure_recovery.ResetSessionBoundary();
  ASSERT_TRUE(secure_recovery.DispatchKey(&secure_client, Character('a'),
                                          after_secure_context, false,
                                          &secure_output));
  secure_recovery.RecordSuccessfulKey(Character('a'), secure_output, false);
  EXPECT_EQ(PreeditValue(secure_output), "あ");
  const StatusTuple unknown_status = ProjectStatus(secure_output);
  EXPECT_TRUE(unknown_status.present);
  EXPECT_EQ(unknown_status.scope, commands::GrimodexSessionStatus::OFF);
  EXPECT_TRUE(unknown_status.digest.empty());

  commands::SessionCommand leave_unknown;
  leave_unknown.set_type(commands::SessionCommand::REVERT);
  commands::Output left_unknown;
  ASSERT_TRUE(fcitx::SendCommandWithGrimodexContext(
      &secure_client, leave_unknown, after_secure_context, &left_unknown));
  secure_recovery.ClearReading();

  // The exact allowlisted application at epoch 4 gets a new project pin.
  const commands::Context restored_project_context =
      fcitx::BuildGrimodexContext("grimodex", "wayland", false, 4);
  secure_recovery.ResetSessionBoundary();
  ASSERT_TRUE(secure_recovery.DispatchKey(
      &secure_client, Character('a'), restored_project_context, false,
      &secure_output));
  secure_recovery.RecordSuccessfulKey(Character('a'), secure_output, false);
  EXPECT_EQ(PreeditValue(secure_output), "あ");
  EXPECT_EQ(ProjectStatus(secure_output).scope,
            commands::GrimodexSessionStatus::PROJECT);
  EXPECT_EQ(ProjectStatus(secure_output).digest, digest_a_);

  // Race atomic protocol publications against four independent sessions.
  // The writer's first B publication is held until at least one client has
  // pinned A with its first key.  Every PROJECT composition then converts the
  // actual yomi and must expose the surface matching its immutable digest.
  std::atomic<bool> start = false;
  std::atomic<bool> first_racing_publication_done = false;
  std::atomic<bool> writer_ok = true;
  std::atomic<int> publish_count = 0;
  std::atomic<int> overlapping_publications = 0;
  std::atomic<int> active_pinned_compositions = 0;
  std::atomic<int> project_observations = 0;
  std::atomic<int> project_a_observations = 0;
  std::atomic<int> project_b_observations = 0;
  std::atomic<int> project_b_pins = 0;
  std::atomic<int> off_observations = 0;
  std::atomic<int> client_failures = 0;
  std::atomic<int> first_client_failure_stage = 0;
  std::thread writer([&] {
    while (!start.load(std::memory_order_acquire)) {
      std::this_thread::yield();
    }
    const absl::Time pin_deadline = absl::Now() + absl::Seconds(5);
    while (active_pinned_compositions.load(std::memory_order_acquire) == 0 &&
           absl::Now() < pin_deadline) {
      std::this_thread::yield();
    }
    if (active_pinned_compositions.load(std::memory_order_acquire) == 0) {
      writer_ok.store(false, std::memory_order_relaxed);
      first_racing_publication_done.store(true, std::memory_order_release);
      return;
    }
    for (int iteration = 0; iteration < 160; ++iteration) {
      // The fixture starts at A, so the synchronization publication is B.
      const bool use_a = (iteration % 2) != 0;
      if (!Publish(use_a ? project_a_ : project_b_,
                   use_a ? "2026-07-17T00:00:00.000Z"
                         : "2026-07-17T00:00:01.000Z")) {
        writer_ok.store(false, std::memory_order_relaxed);
        first_racing_publication_done.store(true,
                                            std::memory_order_release);
        return;
      }
      ++publish_count;
      if (active_pinned_compositions.load(std::memory_order_acquire) > 0) {
        ++overlapping_publications;
      }
      if (iteration == 0) {
        first_racing_publication_done.store(true,
                                            std::memory_order_release);
        // Hold B until a client pins it.  This makes both sides of the
        // publication observable before the remaining A/B churn resumes.
        const absl::Time b_pin_deadline = absl::Now() + absl::Seconds(58);
        while (project_b_pins.load(std::memory_order_acquire) == 0 &&
               absl::Now() < b_pin_deadline) {
          absl::SleepFor(absl::Milliseconds(1));
        }
        if (project_b_pins.load(std::memory_order_acquire) == 0) {
          writer_ok.store(false, std::memory_order_relaxed);
          return;
        }
      }
      std::this_thread::yield();
    }
  });

  std::vector<std::thread> clients;
  for (int client_index = 0; client_index < 4; ++client_index) {
    clients.emplace_back([&, client_index] {
      RuntimeFcitxClient client(state_);
      fcitx::RawReadingRecovery recovery = MakeRuntimeRecovery();
      const commands::Context context = fcitx::BuildGrimodexContext(
          "grimodex", absl::StrCat("wayland-race-", client_index), false,
          static_cast<uint64_t>(10 + client_index));
      if (!Activate(&client, context)) {
        ++client_failures;
        return;
      }
      while (!start.load(std::memory_order_acquire)) {
        std::this_thread::yield();
      }
      for (int iteration = 0; iteration < 12; ++iteration) {
        commands::Output output;
        if (!recovery.DispatchKey(&client, Character('s'), context, false,
                                  &output)) {
          int empty = 0;
          first_client_failure_stage.compare_exchange_strong(empty, 1);
          ++client_failures;
          return;
        }
        recovery.RecordSuccessfulKey(Character('s'), output, false);
        const StatusTuple pinned_status = ProjectStatus(output);
        ++active_pinned_compositions;

        bool composition_ok = true;
        int failure_stage = 0;
        const auto fail = [&](int stage) {
          composition_ok = false;
          if (failure_stage == 0) {
            failure_stage = stage;
          }
        };
        if (iteration == 0) {
          const absl::Time publication_deadline =
              absl::Now() + absl::Seconds(5);
          while (!first_racing_publication_done.load(
                     std::memory_order_acquire) &&
                 absl::Now() < publication_deadline) {
            std::this_thread::yield();
          }
          if (!first_racing_publication_done.load(std::memory_order_acquire)) {
            fail(2);
          }
        }

        const bool valid_project =
            pinned_status.present &&
            pinned_status.scope == commands::GrimodexSessionStatus::PROJECT &&
            (pinned_status.digest == digest_a_ ||
             pinned_status.digest == digest_b_) &&
            pinned_status.sequence > 0;
        const bool valid_off =
            pinned_status.present &&
            pinned_status.scope == commands::GrimodexSessionStatus::OFF &&
            pinned_status.digest.empty();
        if (!valid_project && !valid_off) {
          fail(3);
        }
        if (valid_project && pinned_status.digest == digest_b_) {
          ++project_b_pins;
        }

        for (const char key : std::string("etsuna")) {
          if (!composition_ok) {
            break;
          }
          if (!recovery.DispatchKey(&client, Character(key), context, false,
                                    &output)) {
            fail(4);
            break;
          }
          recovery.RecordSuccessfulKey(Character(key), output, false);
          if (!(ProjectStatus(output) == pinned_status)) {
            fail(5);
            break;
          }
        }
        if (composition_ok && PreeditValue(output) != "せつな") {
          fail(6);
        }

        commands::KeyEvent convert;
        convert.set_special_key(commands::KeyEvent::SPACE);
        if (composition_ok &&
            recovery.DispatchKey(&client, convert, context, false, &output)) {
          recovery.RecordSuccessfulKey(convert, output, false);
          if (!(ProjectStatus(output) == pinned_status)) {
            fail(5);
          }
          // The first conversion intentionally keeps the candidate window
          // closed in this configuration.  The second SPACE opens it.
          if (composition_ok &&
              recovery.DispatchKey(&client, convert, context, false,
                                   &output)) {
            recovery.RecordSuccessfulKey(convert, output, false);
          } else {
            fail(7);
          }
          if (composition_ok &&
              (!(ProjectStatus(output) == pinned_status) ||
               !output.has_candidate_window())) {
            fail(8);
          }
          if (composition_ok && valid_project) {
            const bool pinned_a = pinned_status.digest == digest_a_;
            const std::string_view expected = pinned_a ? "刹那A" : "刹那B";
            const std::string_view forbidden = pinned_a ? "刹那B" : "刹那A";
            const bool has_expected =
                CandidateWindowContains(output.candidate_window(), expected);
            const bool has_forbidden =
                CandidateWindowContains(output.candidate_window(), forbidden);
            if (!has_expected || has_forbidden) {
              fail(9);
            }
            if (composition_ok) {
              ++project_observations;
              if (pinned_a) {
                ++project_a_observations;
              } else {
                ++project_b_observations;
              }
            }
          } else if (composition_ok) {
            const bool leaked_a =
                CandidateWindowContains(output.candidate_window(), "刹那A");
            const bool leaked_b =
                CandidateWindowContains(output.candidate_window(), "刹那B");
            if (leaked_a || leaked_b) {
              fail(10);
            }
            if (composition_ok) {
              ++off_observations;
            }
          }
        } else {
          fail(7);
        }

        commands::SessionCommand revert;
        revert.set_type(commands::SessionCommand::REVERT);
        commands::Output reverted;
        if (composition_ok && !fcitx::SendCommandWithGrimodexContext(
                                  &client, revert, context, &reverted)) {
          fail(11);
        }
        --active_pinned_compositions;
        recovery.ClearReading();
        if (!composition_ok) {
          int empty = 0;
          first_client_failure_stage.compare_exchange_strong(empty,
                                                              failure_stage);
          ++client_failures;
          return;
        }
      }
    });
  }
  start.store(true, std::memory_order_release);
  for (std::thread& client : clients) {
    client.join();
  }
  writer.join();
  EXPECT_TRUE(writer_ok.load());
  EXPECT_EQ(publish_count.load(), 160);
  EXPECT_GT(overlapping_publications.load(), 0);
  EXPECT_GT(project_observations.load(), 0);
  EXPECT_GT(project_a_observations.load(), 0);
  EXPECT_GT(project_b_observations.load(), 0);
  EXPECT_EQ(project_observations.load() + off_observations.load(), 4 * 12);
  EXPECT_EQ(first_client_failure_stage.load(), 0);
  EXPECT_EQ(client_failures.load(), 0);
}

}  // namespace
}  // namespace mozc::unix_runtime_test
