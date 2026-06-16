#include "session/zenz_named_pipe_client.h"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include "absl/time/clock.h"
#include "absl/time/time.h"

#if defined(_WIN32)
#include <windows.h>
#else
#include <fcntl.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace mozc {
namespace session {
namespace {

constexpr uint32_t kZenzWireMagic = 0x315A4E5A;  // "ZNZ1"
constexpr uint16_t kZenzWireVersion = 1;
constexpr uint16_t kZenzWireKindRequest = 1;
constexpr uint16_t kZenzWireKindResponse = 2;

#pragma pack(push, 1)
struct ZenzWireRequestHeader {
  uint32_t magic;
  uint16_t version;
  uint16_t kind;
  uint32_t generation;
  uint32_t timeout_msec;
  uint32_t max_output_chars;
  uint32_t prompt_size;
};

struct ZenzWireResponseHeader {
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

#if defined(_WIN32)

using ZenzSocketHandle = HANDLE;
const ZenzSocketHandle kInvalidZenzSocket = INVALID_HANDLE_VALUE;

void CloseZenzSocket(ZenzSocketHandle handle) {
  ::CloseHandle(handle);
}

std::wstring Utf8ToWidePipeName(const std::string& s) {
  if (s.empty()) {
    return L"";
  }

  const int size = ::MultiByteToWideChar(
      CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
  if (size <= 0) {
    return L"";
  }

  std::wstring output(size, L'\0');
  ::MultiByteToWideChar(
      CP_UTF8, 0, s.data(), static_cast<int>(s.size()), output.data(), size);
  return output;
}

void ZenzPipeDebugOutput(const std::wstring& message) {
  std::wstring line = L"[zenz-pipe] ";
  line.append(message);
  line.push_back(L'\n');
  ::OutputDebugStringW(line.c_str());
}

std::wstring RedactedStatsWide(const wchar_t* label, size_t bytes) {
  std::wstring output(label);
  output.append(L"_bytes=");
  output.append(std::to_wstring(bytes));
  return output;
}

ZenzSocketHandle TryOpenPipeOnce(const std::wstring& pipe_name) {
  return ::CreateFileW(
      pipe_name.c_str(),
      GENERIC_READ | GENERIC_WRITE,
      0,
      nullptr,
      OPEN_EXISTING,
      FILE_ATTRIBUTE_NORMAL,
      nullptr);
}

std::wstring GetCurrentModuleDirectory() {
  wchar_t path[MAX_PATH] = {};
  const DWORD size = ::GetModuleFileNameW(nullptr, path, MAX_PATH);
  if (size == 0 || size >= MAX_PATH) {
    return L".";
  }

  std::wstring full(path, size);
  const size_t pos = full.find_last_of(L"\\/");
  if (pos == std::wstring::npos) {
    return L".";
  }

  return full.substr(0, pos);
}

std::wstring JoinPath(const std::wstring& dir, const std::wstring& file) {
  if (dir.empty()) {
    return file;
  }
  if (dir.back() == L'\\' || dir.back() == L'/') {
    return dir + file;
  }
  return dir + L"\\" + file;
}

bool LaunchZenzScorerIfNeeded() {
  static std::atomic<DWORD> last_launch_tick{0};
  constexpr DWORD kLaunchThrottleMsec = 2000;

  const DWORD now = ::GetTickCount();
  DWORD previous = last_launch_tick.load();

  if (previous != 0 && now - previous < kLaunchThrottleMsec) {
    ZenzPipeDebugOutput(
        std::wstring(L"scorer launch throttled elapsed_msec=")
            .append(std::to_wstring(now - previous)));
    return false;
  }

  while (!last_launch_tick.compare_exchange_weak(previous, now)) {
    if (previous != 0 && now - previous < kLaunchThrottleMsec) {
      ZenzPipeDebugOutput(
          std::wstring(L"scorer launch throttled elapsed_msec=")
              .append(std::to_wstring(now - previous)));
      return false;
    }
  }

  const std::wstring dir = GetCurrentModuleDirectory();
  const std::wstring scorer_path = JoinPath(dir, L"mozc_zenz_scorer.exe");

  const DWORD attr = ::GetFileAttributesW(scorer_path.c_str());
  if (attr == INVALID_FILE_ATTRIBUTES ||
      (attr & FILE_ATTRIBUTE_DIRECTORY) != 0) {
    ZenzPipeDebugOutput(L"scorer not found");
    return false;
  }

  std::wstring command_line = L"\"";
  command_line.append(scorer_path);
  command_line.append(L"\"");

  std::vector<wchar_t> command_line_buffer(
      command_line.begin(), command_line.end());
  command_line_buffer.push_back(L'\0');

  STARTUPINFOW startup = {};
  startup.cb = sizeof(startup);
  startup.dwFlags = STARTF_USESHOWWINDOW;
  startup.wShowWindow = SW_HIDE;

  PROCESS_INFORMATION process = {};

  ZenzPipeDebugOutput(L"launch scorer");

  if (!::CreateProcessW(
          nullptr,
          command_line_buffer.data(),
          nullptr,
          nullptr,
          FALSE,
          CREATE_NO_WINDOW,
          nullptr,
          dir.c_str(),
          &startup,
          &process)) {
    const DWORD error = ::GetLastError();
    ZenzPipeDebugOutput(
        std::wstring(L"launch scorer failed error=")
            .append(std::to_wstring(error)));
    return false;
  }

  ::CloseHandle(process.hThread);
  ::CloseHandle(process.hProcess);
  return true;
}

ZenzSocketHandle OpenPipeWithAutoLaunch(const std::wstring& pipe_name,
                                        uint32_t timeout_msec) {
  ZenzPipeDebugOutput(
      std::wstring(L"CreateFileW begin ")
          .append(RedactedStatsWide(
              L"pipe_name", pipe_name.size() * sizeof(wchar_t))));

  ZenzSocketHandle pipe = TryOpenPipeOnce(pipe_name);

  if (pipe != kInvalidZenzSocket) {
    ZenzPipeDebugOutput(L"CreateFileW succeeded");
    return pipe;
  }

  const DWORD error = ::GetLastError();
  ZenzPipeDebugOutput(
      std::wstring(L"CreateFileW failed error=")
          .append(std::to_wstring(error)));

  if (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) {
    const bool launched = LaunchZenzScorerIfNeeded();

    ZenzPipeDebugOutput(
        std::wstring(L"scorer launch requested launched=")
            .append(launched ? L"true" : L"false"));

    constexpr uint32_t kColdStartRetryMsec = 50;
    const uint32_t retry_budget_msec =
        std::max<uint32_t>(timeout_msec, kColdStartRetryMsec);
    const uint32_t retry_attempts =
        std::max<uint32_t>(1, retry_budget_msec / kColdStartRetryMsec);

    for (uint32_t attempt = 0; attempt < retry_attempts; ++attempt) {
      ::Sleep(kColdStartRetryMsec);

      pipe = TryOpenPipeOnce(pipe_name);
      if (pipe != kInvalidZenzSocket) {
        ZenzPipeDebugOutput(
            std::wstring(L"CreateFileW succeeded after cold start retry attempt=")
                .append(std::to_wstring(attempt + 1)));
        return pipe;
      }

      const DWORD retry_error = ::GetLastError();
      if (retry_error == ERROR_PIPE_BUSY) {
        ::WaitNamedPipeW(pipe_name.c_str(), kColdStartRetryMsec);

        pipe = TryOpenPipeOnce(pipe_name);
        if (pipe != kInvalidZenzSocket) {
          ZenzPipeDebugOutput(
              std::wstring(L"CreateFileW succeeded after cold start busy retry attempt=")
                  .append(std::to_wstring(attempt + 1)));
          return pipe;
        }
      }

      if (attempt == 0 || attempt + 1 == retry_attempts) {
        ZenzPipeDebugOutput(
            std::wstring(L"CreateFileW cold start retry failed attempt=")
                .append(std::to_wstring(attempt + 1))
                .append(L" error=")
                .append(std::to_wstring(retry_error)));
      }
    }

    ZenzPipeDebugOutput(
        std::wstring(L"CreateFileW cold start retry exhausted budget_msec=")
            .append(std::to_wstring(retry_budget_msec)));
    return kInvalidZenzSocket;
  }

  if (error == ERROR_PIPE_BUSY) {
    constexpr int kBusyRetryAttempts = 3;
    constexpr int kBusyRetryMsec = 20;

    for (int attempt = 0; attempt < kBusyRetryAttempts; ++attempt) {
      ::WaitNamedPipeW(pipe_name.c_str(), kBusyRetryMsec);

      ZenzPipeDebugOutput(
          std::wstring(L"CreateFileW retry attempt=")
              .append(std::to_wstring(attempt + 1)));

      pipe = TryOpenPipeOnce(pipe_name);

      if (pipe != kInvalidZenzSocket) {
        ZenzPipeDebugOutput(L"CreateFileW succeeded after retry");
        return pipe;
      }
    }
  }

  return kInvalidZenzSocket;
}

bool WriteAll(ZenzSocketHandle handle, const void* data, uint32_t size) {
  const uint8_t* ptr = static_cast<const uint8_t*>(data);
  uint32_t remaining = size;

  while (remaining > 0) {
    DWORD written = 0;
    if (!::WriteFile(handle, ptr, remaining, &written, nullptr)) {
      return false;
    }
    if (written == 0) {
      return false;
    }
    ptr += written;
    remaining -= written;
  }

  return true;
}

bool ReadAll(ZenzSocketHandle handle, void* data, uint32_t size) {
  uint8_t* ptr = static_cast<uint8_t*>(data);
  uint32_t remaining = size;

  while (remaining > 0) {
    DWORD read = 0;
    if (!::ReadFile(handle, ptr, remaining, &read, nullptr)) {
      return false;
    }
    if (read == 0) {
      return false;
    }
    ptr += read;
    remaining -= read;
  }

  return true;
}

#else

using ZenzSocketHandle = int;
const ZenzSocketHandle kInvalidZenzSocket = -1;

void CloseZenzSocket(ZenzSocketHandle handle) {
  if (handle != kInvalidZenzSocket) {
    ::close(handle);
  }
}

ZenzSocketHandle TryOpenPipeOnce(const std::string& pipe_name) {
  int sock = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (sock < 0) {
    return kInvalidZenzSocket;
  }

  struct sockaddr_un addr = {};
  addr.sun_family = AF_UNIX;
  if (pipe_name.size() >= sizeof(addr.sun_path)) {
    ::close(sock);
    return kInvalidZenzSocket;
  }
  std::strncpy(addr.sun_path, pipe_name.c_str(), sizeof(addr.sun_path) - 1);

  if (::connect(sock, reinterpret_cast<struct sockaddr*>(&addr),
                sizeof(addr)) < 0) {
    ::close(sock);
    return kInvalidZenzSocket;
  }

  return sock;
}

bool LaunchZenzScorerIfNeeded() {
  static std::atomic<uint64_t> last_launch_tick{0};
  constexpr uint64_t kLaunchThrottleMsec = 2000;

  const uint64_t now = absl::ToUnixMillis(absl::Now());
  uint64_t previous = last_launch_tick.load();

  if (previous != 0 && now - previous < kLaunchThrottleMsec) {
    return false;
  }

  while (!last_launch_tick.compare_exchange_weak(previous, now)) {
    if (previous != 0 && now - previous < kLaunchThrottleMsec) {
      return false;
    }
  }

  char exe_path[4096];
  ssize_t len = ::readlink("/proc/self/exe", exe_path, sizeof(exe_path) - 1);
  std::string dir = ".";
  if (len > 0) {
    exe_path[len] = '\0';
    std::string path(exe_path);
    size_t pos = path.find_last_of('/');
    if (pos != std::string::npos) {
      dir = path.substr(0, pos);
    }
  }

  std::string scorer_path = dir + "/mozc_zenz_scorer";
  if (::access(scorer_path.c_str(), X_OK) != 0) {
    return false;
  }

  pid_t pid = ::fork();
  if (pid == 0) {
    // Child process: double fork to avoid zombies
    pid_t pid2 = ::fork();
    if (pid2 == 0) {
      ::setsid(); // Detach from session

      // Redirect stdio to /dev/null
      int fd = ::open("/dev/null", O_RDWR);
      if (fd >= 0) {
        ::dup2(fd, STDIN_FILENO);
        ::dup2(fd, STDOUT_FILENO);
        ::dup2(fd, STDERR_FILENO);
        if (fd > 2) {
          ::close(fd);
        }
      }

      ::execl(scorer_path.c_str(), "mozc_zenz_scorer", nullptr);
      ::_exit(127);
    }
    ::_exit(0);
  } else if (pid > 0) {
    // Parent process: wait for the first child
    ::waitpid(pid, nullptr, 0);
    return true;
  }

  return false;
}

ZenzSocketHandle OpenPipeWithAutoLaunch(const std::string& pipe_name,
                                        uint32_t timeout_msec) {
  ZenzSocketHandle pipe = TryOpenPipeOnce(pipe_name);
  if (pipe != kInvalidZenzSocket) {
    return pipe;
  }

  int err = errno;
  if (err == ENOENT || err == ECONNREFUSED) {
    LaunchZenzScorerIfNeeded();

    constexpr uint32_t kColdStartRetryMsec = 50;
    const uint32_t retry_budget_msec =
        std::max<uint32_t>(timeout_msec, kColdStartRetryMsec);
    const uint32_t retry_attempts =
        std::max<uint32_t>(1, retry_budget_msec / kColdStartRetryMsec);

    for (uint32_t attempt = 0; attempt < retry_attempts; ++attempt) {
      absl::SleepFor(absl::Milliseconds(kColdStartRetryMsec));

      pipe = TryOpenPipeOnce(pipe_name);
      if (pipe != kInvalidZenzSocket) {
        return pipe;
      }
    }
  }

  return kInvalidZenzSocket;
}

bool WriteAll(ZenzSocketHandle handle, const void* data, uint32_t size) {
#if defined(__APPLE__)
  constexpr int kSendFlags = 0;
#else
  constexpr int kSendFlags = MSG_NOSIGNAL;
#endif
  const uint8_t* ptr = static_cast<const uint8_t*>(data);
  uint32_t remaining = size;

  while (remaining > 0) {
    ssize_t written = ::send(handle, ptr, remaining, kSendFlags);
    if (written <= 0) {
      if (errno == EINTR) {
        continue;
      }
      return false;
    }
    ptr += written;
    remaining -= written;
  }

  return true;
}

bool ReadAll(ZenzSocketHandle handle, void* data, uint32_t size) {
  uint8_t* ptr = static_cast<uint8_t*>(data);
  uint32_t remaining = size;

  while (remaining > 0) {
    ssize_t read_bytes = ::recv(handle, ptr, remaining, 0);
    if (read_bytes <= 0) {
      if (errno == EINTR) {
        continue;
      }
      return false;
    }
    ptr += read_bytes;
    remaining -= read_bytes;
  }

  return true;
}

#endif

}  // namespace

bool ZenzNamedPipeClient::IsAvailable() const {
  return true;
}

ZenzLiveResponse ZenzNamedPipeClient::Convert(
    const ZenzLiveRequest& request) {
  ZenzLiveResponse response;
  response.generation = request.generation;
  response.key = request.key;

#if defined(_WIN32)
  ZenzPipeDebugOutput(
      std::wstring(L"Convert entered generation=")
          .append(std::to_wstring(request.generation))
          .append(L" ")
          .append(RedactedStatsWide(L"key", request.key.size()))
          .append(L" ")
          .append(RedactedStatsWide(L"prompt", request.prompt.size()))
          .append(L" timeout_msec=")
          .append(std::to_wstring(request.timeout_msec)));
#endif

  const absl::Time start = absl::Now();

#if defined(_WIN32)
  const std::wstring pipe_name = Utf8ToWidePipeName(request.pipe_name);
  if (pipe_name.empty()) {
    response.ok = false;
    response.debug = "invalid_pipe_name";
    return response;
  }
#else
  std::string pipe_name = request.pipe_name;
  if (pipe_name.rfind("\\\\.\\pipe\\", 0) == 0) {
    pipe_name = std::string(getenv("HOME") ? getenv("HOME") : "/tmp") + "/.mozc_zenz_scorer_pipe";
  }
  if (pipe_name.empty()) {
    response.ok = false;
    response.debug = "invalid_pipe_name";
    return response;
  }
#endif

  ZenzSocketHandle pipe = OpenPipeWithAutoLaunch(pipe_name, request.timeout_msec);

  if (pipe == kInvalidZenzSocket) {
    response.ok = false;
    response.debug = "pipe_open_failed";
    return response;
  }

  ZenzWireRequestHeader request_header = {};
  request_header.magic = kZenzWireMagic;
  request_header.version = kZenzWireVersion;
  request_header.kind = kZenzWireKindRequest;
  request_header.generation = request.generation;
  request_header.timeout_msec = request.timeout_msec;
  request_header.max_output_chars = request.max_output_chars;
  request_header.prompt_size = static_cast<uint32_t>(request.prompt.size());

  bool ok = WriteAll(pipe, &request_header, sizeof(request_header));
  if (ok && !request.prompt.empty()) {
    ok = WriteAll(pipe, request.prompt.data(),
                  static_cast<uint32_t>(request.prompt.size()));
  }

  if (!ok) {
    CloseZenzSocket(pipe);
    response.ok = false;
    response.debug = "pipe_write_failed";
    return response;
  }

  ZenzWireResponseHeader response_header = {};
  ok = ReadAll(pipe, &response_header, sizeof(response_header));
  if (!ok) {
    CloseZenzSocket(pipe);
    response.ok = false;
    response.debug = "pipe_read_header_failed";
    return response;
  }

  if (response_header.magic != kZenzWireMagic ||
      response_header.version != kZenzWireVersion ||
      response_header.kind != kZenzWireKindResponse ||
      response_header.generation != request.generation) {
    CloseZenzSocket(pipe);
    response.ok = false;
    response.debug = "pipe_response_header_invalid";
    return response;
  }

  std::string value(response_header.value_size, '\0');
  if (response_header.value_size > 0) {
    ok = ReadAll(pipe, value.data(), response_header.value_size);
  }

  std::string debug(response_header.debug_size, '\0');
  if (ok && response_header.debug_size > 0) {
    ok = ReadAll(pipe, debug.data(), response_header.debug_size);
  }

  CloseZenzSocket(pipe);

  if (!ok) {
    response.ok = false;
    response.debug = "pipe_read_payload_failed";
    return response;
  }

#if defined(_WIN32)
  ZenzPipeDebugOutput(
      std::wstring(L"Convert response status=")
          .append(std::to_wstring(response_header.status))
          .append(L" latency_msec=")
          .append(std::to_wstring(response_header.latency_msec))
          .append(L" ")
          .append(RedactedStatsWide(L"value", value.size()))
          .append(L" ")
          .append(RedactedStatsWide(L"debug", debug.size())));
#endif

  response.ok = response_header.status == 0;
  response.timeout = response_header.status == 2;
  response.value = std::move(value);
  response.debug = std::move(debug);
  response.latency = absl::Now() - start;
  return response;
}

}  // namespace session
}  // namespace mozc
