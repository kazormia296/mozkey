// Copyright 2026 The Mozkey Authors

#include "grimodex/protocol_v1_secure_reader.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <array>
#include <cerrno>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/string_view.h"
#include "grimodex/protocol_v1.h"

namespace mozc::grimodex {
namespace {

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
  UniqueFd(UniqueFd &&other) noexcept : fd_(std::exchange(other.fd_, -1)) {}
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

absl::Status OpenError(absl::string_view operation, int error_number) {
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

bool IsSafeIdentifier(absl::string_view value, size_t maximum) {
  if (value.empty() || value.size() > maximum) {
    return false;
  }
  for (const unsigned char byte : value) {
    const bool alphabetic = (byte >= 'A' && byte <= 'Z') ||
                            (byte >= 'a' && byte <= 'z');
    if (!alphabetic && !(byte >= '0' && byte <= '9') && byte != '-' &&
        byte != '_') {
      return false;
    }
  }
  return true;
}

absl::Status ValidateDirectory(const struct stat &metadata,
                               absl::string_view label) {
  if (!S_ISDIR(metadata.st_mode)) {
    return absl::FailedPreconditionError(
        absl::StrCat(label, " is not a directory"));
  }
  if (metadata.st_uid != geteuid()) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " is not owned by the effective user"));
  }
  if ((metadata.st_mode & 0777) != 0700) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " must have mode 0700"));
  }
  return absl::OkStatus();
}

absl::StatusOr<std::vector<std::string>> SplitAbsolutePath(
    absl::string_view path) {
  if (path.empty() || path.front() != '/') {
    return absl::InvalidArgumentError("Grimodex root must be absolute");
  }
  std::vector<std::string> result;
  size_t begin = 1;
  while (begin < path.size()) {
    const size_t end = path.find('/', begin);
    const size_t length =
        end == absl::string_view::npos ? path.size() - begin : end - begin;
    if (length == 0) {
      return absl::InvalidArgumentError(
          "Grimodex root contains an empty path component");
    }
    const absl::string_view component = path.substr(begin, length);
    if (component == "." || component == "..") {
      return absl::InvalidArgumentError(
          "Grimodex root contains a traversal component");
    }
    result.emplace_back(component);
    if (end == absl::string_view::npos) {
      break;
    }
    begin = end + 1;
    if (begin == path.size()) {
      return absl::InvalidArgumentError(
          "Grimodex root must not have a trailing slash");
    }
  }
  if (result.empty()) {
    return absl::InvalidArgumentError("filesystem root cannot be an IME root");
  }
  return result;
}

absl::StatusOr<UniqueFd> OpenSecureRoot(absl::string_view path) {
  absl::StatusOr<std::vector<std::string>> components =
      SplitAbsolutePath(path);
  if (!components.ok()) {
    return components.status();
  }

  UniqueFd current(open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
  if (current.get() < 0) {
    return OpenError("open filesystem root", errno);
  }
  for (const std::string &component : *components) {
    const int next = openat(current.get(), component.c_str(),
                            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (next < 0) {
      return OpenError(absl::StrCat("open root component ", component), errno);
    }
    current = UniqueFd(next);
  }

  struct stat metadata = {};
  if (fstat(current.get(), &metadata) != 0) {
    return OpenError("stat Grimodex root", errno);
  }
  if (absl::Status status = ValidateDirectory(metadata, "Grimodex root");
      !status.ok()) {
    return status;
  }
  return current;
}

absl::StatusOr<UniqueFd> OpenSecureProjects(int root_fd) {
  const int fd = openat(root_fd, "projects",
                        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0) {
    return OpenError("open projects directory", errno);
  }
  UniqueFd projects(fd);
  struct stat metadata = {};
  if (fstat(projects.get(), &metadata) != 0) {
    return OpenError("stat projects directory", errno);
  }
  if (absl::Status status =
          ValidateDirectory(metadata, "Grimodex projects directory");
      !status.ok()) {
    return status;
  }
  return projects;
}

bool SameFileVersion(const struct stat &first, const struct stat &second) {
  return first.st_dev == second.st_dev && first.st_ino == second.st_ino &&
         first.st_size == second.st_size &&
         first.st_mtim.tv_sec == second.st_mtim.tv_sec &&
         first.st_mtim.tv_nsec == second.st_mtim.tv_nsec &&
         first.st_ctim.tv_sec == second.st_ctim.tv_sec &&
         first.st_ctim.tv_nsec == second.st_ctim.tv_nsec;
}

absl::Status ValidateFile(const struct stat &metadata,
                          absl::string_view label, size_t max_bytes) {
  if (!S_ISREG(metadata.st_mode)) {
    return absl::FailedPreconditionError(
        absl::StrCat(label, " is not a regular file"));
  }
  if (metadata.st_uid != geteuid()) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " is not owned by the effective user"));
  }
  if ((metadata.st_mode & 0077) != 0) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " grants group or other permissions"));
  }
  if (metadata.st_nlink != 1) {
    return absl::PermissionDeniedError(
        absl::StrCat(label, " must have exactly one link"));
  }
  if (metadata.st_size < 0 ||
      static_cast<uint64_t>(metadata.st_size) > max_bytes) {
    return absl::ResourceExhaustedError(
        absl::StrCat(label, " exceeds the Protocol V1 byte limit"));
  }
  return absl::OkStatus();
}

absl::StatusOr<VerifiedFileBytes> ReadSecureFile(
    int directory_fd, absl::string_view filename, size_t max_bytes) {
  const std::string filename_string(filename);
  const int fd = openat(directory_fd, filename_string.c_str(),
                        O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
  if (fd < 0) {
    return OpenError(absl::StrCat("open ", filename), errno);
  }
  UniqueFd file(fd);

  struct stat before = {};
  if (fstat(file.get(), &before) != 0) {
    return OpenError(absl::StrCat("stat ", filename), errno);
  }
  if (absl::Status status = ValidateFile(before, filename, max_bytes);
      !status.ok()) {
    return status;
  }

  std::string bytes;
  bytes.reserve(static_cast<size_t>(before.st_size));
  std::array<char, 16 * 1024> buffer;
  while (true) {
    const ssize_t count = read(file.get(), buffer.data(), buffer.size());
    if (count < 0) {
      if (errno == EINTR) {
        continue;
      }
      return OpenError(absl::StrCat("read ", filename), errno);
    }
    if (count == 0) {
      break;
    }
    if (bytes.size() + static_cast<size_t>(count) > max_bytes) {
      return absl::ResourceExhaustedError(
          absl::StrCat(filename, " grew beyond the Protocol V1 byte limit"));
    }
    bytes.append(buffer.data(), static_cast<size_t>(count));
  }

  struct stat after = {};
  if (fstat(file.get(), &after) != 0) {
    return OpenError(absl::StrCat("restat ", filename), errno);
  }
  if (!SameFileVersion(before, after)) {
    return absl::AbortedError(
        absl::StrCat(filename, " changed while it was being read"));
  }
  return VerifiedFileBytes::FromBytes(std::move(bytes));
}

}  // namespace

SecureProtocolV1FileReader::SecureProtocolV1FileReader(std::string root_path)
    : root_path_(std::move(root_path)) {}

absl::StatusOr<VerifiedFileBytes> SecureProtocolV1FileReader::ReadState(
    size_t max_bytes) {
  absl::StatusOr<UniqueFd> root = OpenSecureRoot(root_path_);
  if (!root.ok()) {
    return root.status();
  }
  return ReadSecureFile(root->get(), "state.json", max_bytes);
}

absl::StatusOr<VerifiedFileBytes> SecureProtocolV1FileReader::ReadProject(
    absl::string_view project_id, size_t max_bytes) {
  if (!IsSafeIdentifier(project_id, ProtocolV1Limits::kProjectIdScalars)) {
    return absl::InvalidArgumentError("unsafe Grimodex project ID");
  }
  absl::StatusOr<UniqueFd> root = OpenSecureRoot(root_path_);
  if (!root.ok()) {
    return root.status();
  }
  absl::StatusOr<UniqueFd> projects = OpenSecureProjects(root->get());
  if (!projects.ok()) {
    return projects.status();
  }
  return ReadSecureFile(projects->get(),
                        absl::StrCat(project_id, ".json"), max_bytes);
}

std::string ResolveProtocolV1Root(absl::string_view override_root,
                                  absl::string_view xdg_data_home,
                                  absl::string_view home_directory) {
  if (!override_root.empty()) {
    return std::string(override_root);
  }
  if (!xdg_data_home.empty()) {
    return absl::StrCat(xdg_data_home, "/com.miyakey.grimodex/ime");
  }
  return absl::StrCat(home_directory,
                      "/.local/share/com.miyakey.grimodex/ime");
}

}  // namespace mozc::grimodex
