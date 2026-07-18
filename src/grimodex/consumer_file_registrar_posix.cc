// Copyright 2026 The Mozkey Authors

#include "grimodex/consumer_file_registrar_posix.h"

#if !defined(_WIN32)

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
#include "absl/strings/str_cat.h"
#include "absl/strings/string_view.h"
#include "grimodex/consumer_handshake.h"

namespace mozc::grimodex {
namespace {

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

absl::Status PosixError(absl::string_view operation, int error_number) {
  const std::string message =
      absl::StrCat(operation, " failed (errno=", error_number, ")");
  switch (error_number) {
    case ENOENT:
      return absl::NotFoundError(message);
    case EACCES:
    case EPERM:
    case ELOOP:
      return absl::PermissionDeniedError(message);
    case ENAMETOOLONG:
    case ENOTDIR:
      return absl::InvalidArgumentError(message);
    default:
      return absl::FailedPreconditionError(message);
  }
}

bool IsSafeConsumerId(absl::string_view id) {
  if (id.empty() || id.size() > 64) {
    return false;
  }
  const auto is_alphanumeric = [](unsigned char byte) {
    return (byte >= 'a' && byte <= 'z') ||
           (byte >= '0' && byte <= '9');
  };
  if (!is_alphanumeric(id.front()) || !is_alphanumeric(id.back())) {
    return false;
  }
  for (const unsigned char byte : id) {
    if (!(is_alphanumeric(byte) || byte == '-' || byte == '_' ||
          byte == '.')) {
      return false;
    }
  }
  return true;
}

absl::Status ValidateRoot(absl::string_view root) {
  if (root.empty() || root.front() != '/' || root == "/" ||
      root.back() == '/' || root.find("//") != absl::string_view::npos ||
      root.find('\0') != absl::string_view::npos) {
    return absl::InvalidArgumentError(
        "Grimodex consumer root must be a normalized absolute path");
  }
  size_t begin = 1;
  while (begin < root.size()) {
    const size_t end = root.find('/', begin);
    const absl::string_view component = root.substr(
        begin, end == absl::string_view::npos ? root.size() - begin
                                             : end - begin);
    if (component.empty() || component == "." || component == "..") {
      return absl::InvalidArgumentError(
          "Grimodex consumer root contains an unsafe path component");
    }
    if (end == absl::string_view::npos) {
      break;
    }
    begin = end + 1;
  }
  return absl::OkStatus();
}

absl::Status ForceAndVerifyPrivateDirectory(int fd,
                                            absl::string_view label) {
  struct stat info = {};
  if (fstat(fd, &info) != 0) {
    return PosixError(absl::StrCat("inspect ", label), errno);
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
    return PosixError(absl::StrCat("chmod ", label), errno);
  }
  if (fstat(fd, &info) != 0) {
    return PosixError(absl::StrCat("reinspect ", label), errno);
  }
  if ((info.st_mode & 0777) != 0700 || info.st_uid != geteuid()) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " could not be made private"));
  }
  return absl::OkStatus();
}

absl::StatusOr<UniqueFd> OpenPrivateRoot(const std::string &root,
                                         bool create) {
  if (absl::Status status = ValidateRoot(root); !status.ok()) {
    return status;
  }
  UniqueFd current(
      open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
  if (current.get() < 0) {
    return PosixError("open filesystem root", errno);
  }

  size_t begin = 1;
  while (begin < root.size()) {
    const size_t end = root.find('/', begin);
    const std::string component = root.substr(
        begin, end == std::string::npos ? root.size() - begin
                                       : end - begin);
    if (create && mkdirat(current.get(), component.c_str(), 0700) != 0 &&
        errno != EEXIST) {
      return PosixError("create Grimodex consumer directory tree", errno);
    }
    UniqueFd next(openat(current.get(), component.c_str(),
                         O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    if (next.get() < 0) {
      return PosixError("open Grimodex consumer path component", errno);
    }
    current = std::move(next);
    if (end == std::string::npos) {
      break;
    }
    begin = end + 1;
  }
  if (absl::Status status = ForceAndVerifyPrivateDirectory(
          current.get(), "Grimodex consumer root");
      !status.ok()) {
    return status;
  }
  return current;
}

absl::StatusOr<UniqueFd> OpenConsumersDirectory(int root_fd, bool create) {
  if (create && mkdirat(root_fd, "consumers", 0700) != 0 && errno != EEXIST) {
    return PosixError("create Grimodex consumers directory", errno);
  }
  UniqueFd consumers(openat(root_fd, "consumers",
                            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
  if (consumers.get() < 0) {
    return PosixError("open Grimodex consumers directory", errno);
  }
  if (absl::Status status = ForceAndVerifyPrivateDirectory(
          consumers.get(), "Grimodex consumers directory");
      !status.ok()) {
    return status;
  }
  return consumers;
}

absl::Status InspectConsumerEntry(int directory_fd,
                                  const std::string &filename,
                                  bool allow_missing) {
  struct stat info = {};
  if (fstatat(directory_fd, filename.c_str(), &info,
              AT_SYMLINK_NOFOLLOW) != 0) {
    if (allow_missing && errno == ENOENT) {
      return absl::OkStatus();
    }
    return PosixError("inspect Grimodex consumer handshake", errno);
  }
  if (!S_ISREG(info.st_mode)) {
    return absl::PermissionDeniedError(
        "Grimodex consumer handshake is not a regular file");
  }
  if (info.st_uid != geteuid()) {
    return absl::PermissionDeniedError(
        "Grimodex consumer handshake is not owned by the effective user");
  }
  if (info.st_nlink != 1) {
    return absl::PermissionDeniedError(
        "Grimodex consumer handshake must have exactly one hard link");
  }
  if ((info.st_mode & 0777) != 0600) {
    return absl::PermissionDeniedError(
        "Grimodex consumer handshake is not private");
  }
  return absl::OkStatus();
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
      return PosixError("write Grimodex consumer handshake", errno);
    }
    offset += static_cast<size_t>(count);
  }
  return absl::OkStatus();
}

absl::Status AtomicReplace(int directory_fd, absl::string_view consumer_id,
                           absl::string_view bytes) {
  const std::string destination = absl::StrCat(consumer_id, ".json");
  if (absl::Status status =
          InspectConsumerEntry(directory_fd, destination,
                               /*allow_missing=*/true);
      !status.ok()) {
    return status;
  }

  static std::atomic<uint64_t> nonce = 0;
  std::string temporary_name;
  UniqueFd temporary;
  for (int attempt = 0; attempt < kCreateAttempts; ++attempt) {
    temporary_name = absl::StrCat(".", consumer_id, ".", getpid(), ".",
                                  nonce.fetch_add(1), ".tmp");
    temporary = UniqueFd(openat(directory_fd, temporary_name.c_str(),
                                O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC |
                                    O_NOFOLLOW,
                                0600));
    if (temporary.get() >= 0) {
      break;
    }
    if (errno != EEXIST) {
      return PosixError("create Grimodex consumer temporary file", errno);
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
        PosixError("chmod Grimodex consumer temporary file", errno);
    cleanup();
    return status;
  }
  if (fsync(temporary.get()) != 0) {
    const absl::Status status =
        PosixError("fsync Grimodex consumer temporary file", errno);
    cleanup();
    return status;
  }
  if (renameat(directory_fd, temporary_name.c_str(), directory_fd,
               destination.c_str()) != 0) {
    const absl::Status status =
        PosixError("replace Grimodex consumer handshake", errno);
    cleanup();
    return status;
  }
  renamed = true;
  if (absl::Status status = InspectConsumerEntry(
          directory_fd, destination, /*allow_missing=*/false);
      !status.ok()) {
    return status;
  }
  if (fsync(directory_fd) != 0) {
    return PosixError("fsync Grimodex consumers directory", errno);
  }
  return absl::OkStatus();
}

}  // namespace

PosixConsumerFileRegistrar::PosixConsumerFileRegistrar(std::string root)
    : root_(std::move(root)) {}

absl::Status PosixConsumerFileRegistrar::Refresh(
    const ConsumerHandshake &handshake) const {
  absl::StatusOr<std::string> payload =
      SerializeConsumerHandshake(handshake);
  if (!payload.ok()) {
    return payload.status();
  }
  absl::StatusOr<UniqueFd> root = OpenPrivateRoot(root_, /*create=*/true);
  if (!root.ok()) {
    return root.status();
  }
  absl::StatusOr<UniqueFd> consumers =
      OpenConsumersDirectory(root->get(), /*create=*/true);
  if (!consumers.ok()) {
    return consumers.status();
  }
  return AtomicReplace(consumers->get(), handshake.consumer_id, *payload);
}

absl::Status PosixConsumerFileRegistrar::Unregister(
    absl::string_view consumer_id) const {
  if (!IsSafeConsumerId(consumer_id)) {
    return absl::InvalidArgumentError("invalid Grimodex consumer ID");
  }
  absl::StatusOr<UniqueFd> root = OpenPrivateRoot(root_, /*create=*/false);
  if (absl::IsNotFound(root.status())) {
    return absl::OkStatus();
  }
  if (!root.ok()) {
    return root.status();
  }
  absl::StatusOr<UniqueFd> consumers =
      OpenConsumersDirectory(root->get(), /*create=*/false);
  if (absl::IsNotFound(consumers.status())) {
    return absl::OkStatus();
  }
  if (!consumers.ok()) {
    return consumers.status();
  }
  const std::string filename = absl::StrCat(consumer_id, ".json");
  struct stat info = {};
  if (fstatat(consumers->get(), filename.c_str(), &info,
              AT_SYMLINK_NOFOLLOW) != 0 &&
      errno == ENOENT) {
    return absl::OkStatus();
  }
  if (absl::Status status = InspectConsumerEntry(
          consumers->get(), filename, /*allow_missing=*/false);
      !status.ok()) {
    return status;
  }
  if (unlinkat(consumers->get(), filename.c_str(), 0) != 0) {
    if (errno == ENOENT) {
      return absl::OkStatus();
    }
    return PosixError("remove Grimodex consumer handshake", errno);
  }
  if (fsync(consumers->get()) != 0) {
    return PosixError("fsync Grimodex consumers directory", errno);
  }
  return absl::OkStatus();
}

}  // namespace mozc::grimodex

#endif  // !defined(_WIN32)
