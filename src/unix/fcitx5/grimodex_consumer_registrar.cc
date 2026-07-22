// Copyright 2026 The Mozkey Authors

#include "unix/fcitx5/grimodex_consumer_registrar.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <atomic>
#include <cerrno>
#include <cstdint>
#include <string>
#include <utility>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/ascii.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/string_view.h"
#include "absl/time/time.h"

namespace mozc::fcitx5 {
namespace {

constexpr size_t kMaxHandshakeBytes = 8 * 1024;
constexpr int kCreateAttempts = 32;

class UniqueFd final {
 public:
  explicit UniqueFd(int fd = -1) : fd_(fd) {}
  ~UniqueFd() {
    if (fd_ >= 0) {
      close(fd_);
    }
  }
  UniqueFd(const UniqueFd &) = delete;
  UniqueFd &operator=(const UniqueFd &) = delete;
  UniqueFd(UniqueFd &&other) noexcept
      : fd_(std::exchange(other.fd_, -1)) {}
  UniqueFd &operator=(UniqueFd &&other) noexcept {
    if (this != &other) {
      if (fd_ >= 0) {
        close(fd_);
      }
      fd_ = std::exchange(other.fd_, -1);
    }
    return *this;
  }
  int get() const { return fd_; }

 private:
  int fd_;
};

absl::Status SystemError(absl::string_view operation, int error_number) {
  const std::string message =
      absl::StrCat(operation, " failed (errno=", error_number, ")");
  if (error_number == ENOENT) {
    return absl::NotFoundError(message);
  }
  if (error_number == EACCES || error_number == EPERM ||
      error_number == ELOOP) {
    return absl::PermissionDeniedError(message);
  }
  return absl::FailedPreconditionError(message);
}

absl::Status VerifyPrivateDirectory(int fd, absl::string_view label) {
  struct stat info = {};
  if (fstat(fd, &info) != 0) {
    return SystemError(absl::StrCat("inspect ", label), errno);
  }
  if (!S_ISDIR(info.st_mode)) {
    return absl::FailedPreconditionError(
        absl::StrCat(label, " is not a directory"));
  }
  if (info.st_uid != geteuid()) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " is not owned by the effective user"));
  }
  if (fchmod(fd, 0700) != 0) {
    return SystemError(absl::StrCat("chmod ", label), errno);
  }
  return absl::OkStatus();
}

absl::StatusOr<UniqueFd> OpenPrivateRoot(const std::string &root,
                                         bool create) {
  if (root.empty() || root.front() != '/' || root == "/" ||
      root.back() == '/' || root.find("//") != std::string::npos) {
    return absl::InvalidArgumentError(
        "Grimodex consumer root must be a normalized absolute path");
  }

  UniqueFd current(
      open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
  if (current.get() < 0) {
    return SystemError("open filesystem root", errno);
  }

  size_t begin = 1;
  while (begin < root.size()) {
    const size_t end = root.find('/', begin);
    const absl::string_view component = absl::string_view(root).substr(
        begin, end == absl::string_view::npos ? root.size() - begin
                                             : end - begin);
    if (component.empty() || component == "." || component == "..") {
      return absl::InvalidArgumentError(
          "Grimodex consumer root contains an unsafe path component");
    }

    const std::string name(component);
    if (create && mkdirat(current.get(), name.c_str(), 0700) != 0 &&
        errno != EEXIST) {
      return SystemError("create Grimodex consumer directory tree", errno);
    }
    UniqueFd next(openat(current.get(), name.c_str(),
                         O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    if (next.get() < 0) {
      return SystemError("open Grimodex consumer path component", errno);
    }
    current = std::move(next);
    if (end == absl::string_view::npos) {
      break;
    }
    begin = end + 1;
  }
  if (absl::Status status =
          VerifyPrivateDirectory(current.get(), "Grimodex consumer root");
      !status.ok()) {
    return status;
  }
  return current;
}

absl::StatusOr<UniqueFd> OpenConsumersDirectory(int root_fd) {
  if (mkdirat(root_fd, "consumers", 0700) != 0 && errno != EEXIST) {
    return SystemError("create Grimodex consumers directory", errno);
  }
  UniqueFd fd(openat(root_fd, "consumers",
                     O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
  if (fd.get() < 0) {
    return SystemError("open Grimodex consumers directory", errno);
  }
  if (absl::Status status =
          VerifyPrivateDirectory(fd.get(), "Grimodex consumers directory");
      !status.ok()) {
    return status;
  }
  return fd;
}

bool IsSafeVersion(absl::string_view version) {
  if (version.empty() || version.size() > 64 ||
      absl::StripAsciiWhitespace(version).empty()) {
    return false;
  }
  for (unsigned char byte : version) {
    if (!(absl::ascii_isalnum(byte) || byte == '.' || byte == '-' ||
          byte == '_' || byte == '+')) {
      return false;
    }
  }
  return true;
}

bool IsValidTimestamp(absl::string_view timestamp) {
  if (timestamp.empty() || timestamp.size() > 64 || timestamp.back() != 'Z') {
    return false;
  }
  absl::Time parsed;
  std::string error;
  return absl::ParseTime(absl::RFC3339_full, timestamp, &parsed, &error);
}

absl::Status ValidateRuntimeMarker(absl::string_view path) {
  if (path.empty() || path.front() != '/') {
    return absl::InvalidArgumentError(
        "Mozkey runtime marker must be an absolute path");
  }
  struct stat info = {};
  const std::string path_string(path);
  if (lstat(path_string.c_str(), &info) != 0) {
    return SystemError("inspect Mozkey runtime marker", errno);
  }
  if (!S_ISREG(info.st_mode) || (info.st_mode & 0111) == 0) {
    return absl::FailedPreconditionError(
        "Mozkey runtime marker is not a regular executable");
  }
  return absl::OkStatus();
}

bool IsRegularExecutable(absl::string_view path, bool allow_symlink) {
  const std::string path_string(path);
  struct stat info = {};
  if (lstat(path_string.c_str(), &info) != 0) {
    return false;
  }
  if (S_ISLNK(info.st_mode)) {
    if (!allow_symlink || stat(path_string.c_str(), &info) != 0) {
      return false;
    }
  }
  return S_ISREG(info.st_mode) && (info.st_mode & 0111) != 0;
}

bool IsRegularImmutableData(absl::string_view path) {
  const std::string path_string(path);
  struct stat info = {};
  return lstat(path_string.c_str(), &info) == 0 &&
         S_ISREG(info.st_mode) && info.st_size > 0 &&
         (info.st_mode & 0022) == 0;
}

bool HasCompleteZenzRuntime(absl::string_view runtime_marker) {
  const size_t separator = runtime_marker.rfind('/');
  if (separator == absl::string_view::npos || separator == 0) {
    return false;
  }
  const absl::string_view directory = runtime_marker.substr(0, separator);
  return IsRegularExecutable(
             absl::StrCat(directory, "/mozc_zenz_scorer"),
             /*allow_symlink=*/false) &&
         IsRegularImmutableData(absl::StrCat(
             directory, "/models/zenz-v3.2-small-Q5_K_M.gguf")) &&
         IsRegularExecutable(absl::StrCat(directory, "/llama-server"),
                             /*allow_symlink=*/true);
}

std::string Handshake(absl::string_view version,
                      absl::string_view timestamp,
                      bool zenzai_v3_conditions) {
  return absl::StrCat(
      R"json({"capabilities":{"application_scoping":true,"dynamic_dictionary":true,"profile":true,"zenzai_v3_conditions":)json",
      zenzai_v3_conditions ? "true" : "false",
      R"json(},"consumer_id":"fcitx5-mozkey-ibg","format_version":1,"last_seen":")json",
      timestamp,
      R"json(","name":"Mozkey IbG for Grimodex on Linux","platform":"linux","version":")json",
      version, R"json("})json",
      "\n");
}

absl::Status WriteAll(int fd, absl::string_view bytes) {
  size_t offset = 0;
  while (offset < bytes.size()) {
    const ssize_t count =
        write(fd, bytes.data() + offset, bytes.size() - offset);
    if (count < 0 && errno == EINTR) {
      continue;
    }
    if (count <= 0) {
      return SystemError("write Grimodex consumer handshake", errno);
    }
    offset += static_cast<size_t>(count);
  }
  return absl::OkStatus();
}

absl::Status AtomicReplace(int directory_fd, absl::string_view bytes) {
  static std::atomic<uint64_t> nonce = 0;
  std::string temporary_name;
  UniqueFd temporary;
  for (int attempt = 0; attempt < kCreateAttempts; ++attempt) {
    temporary_name = absl::StrCat(".fcitx5-mozkey-ibg.", getpid(), ".",
                                  nonce.fetch_add(1), ".tmp");
    temporary = UniqueFd(openat(directory_fd, temporary_name.c_str(),
                                O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC |
                                    O_NOFOLLOW,
                                0600));
    if (temporary.get() >= 0) {
      break;
    }
    if (errno != EEXIST) {
      return SystemError("create Grimodex consumer temporary file", errno);
    }
  }
  if (temporary.get() < 0) {
    return absl::ResourceExhaustedError(
        "could not allocate a unique Grimodex consumer temporary file");
  }

  bool renamed = false;
  auto cleanup = [&] {
    if (!renamed) {
      unlinkat(directory_fd, temporary_name.c_str(), 0);
    }
  };
  if (absl::Status status = WriteAll(temporary.get(), bytes); !status.ok()) {
    cleanup();
    return status;
  }
  if (fchmod(temporary.get(), 0600) != 0) {
    const absl::Status status =
        SystemError("chmod Grimodex consumer temporary file", errno);
    cleanup();
    return status;
  }
  if (fsync(temporary.get()) != 0) {
    const absl::Status status =
        SystemError("fsync Grimodex consumer temporary file", errno);
    cleanup();
    return status;
  }
  if (renameat(directory_fd, temporary_name.c_str(), directory_fd,
               "fcitx5-mozkey-ibg.json") != 0) {
    const absl::Status status =
        SystemError("replace Grimodex consumer handshake", errno);
    cleanup();
    return status;
  }
  renamed = true;
  if (fsync(directory_fd) != 0) {
    return SystemError("fsync Grimodex consumers directory", errno);
  }
  return absl::OkStatus();
}

}  // namespace

GrimodexConsumerRegistrar::GrimodexConsumerRegistrar(std::string root)
    : root_(std::move(root)) {}

absl::Status GrimodexConsumerRegistrar::Register(
    absl::string_view version, absl::string_view timestamp) const {
  return RegisterWithCapabilities(version, timestamp,
                                  /*zenzai_v3_conditions=*/true);
}

absl::Status GrimodexConsumerRegistrar::RegisterWithCapabilities(
    absl::string_view version, absl::string_view timestamp,
    bool zenzai_v3_conditions) const {
  if (!IsSafeVersion(version)) {
    return absl::InvalidArgumentError("invalid Mozkey consumer version");
  }
  if (!IsValidTimestamp(timestamp)) {
    return absl::InvalidArgumentError("invalid Mozkey consumer timestamp");
  }
  const std::string payload =
      Handshake(version, timestamp, zenzai_v3_conditions);
  if (payload.size() > kMaxHandshakeBytes) {
    return absl::ResourceExhaustedError(
        "Mozkey consumer handshake exceeds its wire limit");
  }

  absl::StatusOr<UniqueFd> root = OpenPrivateRoot(root_, /*create=*/true);
  if (!root.ok()) {
    return root.status();
  }
  absl::StatusOr<UniqueFd> consumers = OpenConsumersDirectory(root->get());
  if (!consumers.ok()) {
    return consumers.status();
  }
  return AtomicReplace(consumers->get(), payload);
}

absl::Status GrimodexConsumerRegistrar::RefreshIfInstalled(
    absl::string_view version, absl::string_view timestamp,
    absl::string_view runtime_marker) const {
  const absl::Status before = ValidateRuntimeMarker(runtime_marker);
  if (!before.ok()) {
    const absl::Status removal = Unregister();
    if (!removal.ok()) {
      return removal;
    }
    return absl::IsNotFound(before) ? absl::OkStatus() : before;
  }

  if (absl::Status status =
          RegisterWithCapabilities(version, timestamp,
                                   HasCompleteZenzRuntime(runtime_marker));
      !status.ok()) {
    return status;
  }

  const absl::Status after = ValidateRuntimeMarker(runtime_marker);
  if (after.ok()) {
    return absl::OkStatus();
  }
  const absl::Status removal = Unregister();
  if (!removal.ok()) {
    return removal;
  }
  return absl::IsNotFound(after) ? absl::OkStatus() : after;
}

absl::Status GrimodexConsumerRegistrar::Unregister() const {
  absl::StatusOr<UniqueFd> root = OpenPrivateRoot(root_, /*create=*/false);
  if (absl::IsNotFound(root.status())) {
    return absl::OkStatus();
  }
  if (!root.ok()) {
    return root.status();
  }
  UniqueFd consumers(openat(root->get(), "consumers",
                            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
  if (consumers.get() < 0 && errno == ENOENT) {
    return absl::OkStatus();
  }
  if (consumers.get() < 0) {
    return SystemError("open Grimodex consumers directory", errno);
  }
  if (absl::Status status = VerifyPrivateDirectory(
          consumers.get(), "Grimodex consumers directory");
      !status.ok()) {
    return status;
  }
  if (unlinkat(consumers.get(), "fcitx5-mozkey-ibg.json", 0) != 0 &&
      errno != ENOENT) {
    return SystemError("remove Grimodex Mozkey consumer handshake", errno);
  }
  if (fsync(consumers.get()) != 0) {
    return SystemError("fsync Grimodex consumers directory", errno);
  }
  return absl::OkStatus();
}

}  // namespace mozc::fcitx5
