// Copyright 2026 The Mozkey Authors

#include "grimodex/protocol_v1.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <array>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/string_view.h"
#include "google/protobuf/struct.pb.h"
#include "google/protobuf/util/json_util.h"
#include "grimodex/protocol_v1.pb.h"

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

uint32_t RotateRight(uint32_t value, unsigned amount) {
  return (value >> amount) | (value << (32 - amount));
}

std::string Sha256(absl::string_view input) {
  static constexpr std::array<uint32_t, 64> kRoundConstants = {
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b,
      0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
      0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7,
      0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
      0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152,
      0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
      0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
      0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
      0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
      0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
      0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f,
      0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
      0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
  std::array<uint32_t, 8> hash = {0x6a09e667, 0xbb67ae85, 0x3c6ef372,
                                  0xa54ff53a, 0x510e527f, 0x9b05688c,
                                  0x1f83d9ab, 0x5be0cd19};

  std::vector<uint8_t> padded(input.begin(), input.end());
  const uint64_t input_bits = static_cast<uint64_t>(input.size()) * 8;
  padded.push_back(0x80);
  while (padded.size() % 64 != 56) {
    padded.push_back(0);
  }
  for (int shift = 56; shift >= 0; shift -= 8) {
    padded.push_back(static_cast<uint8_t>(input_bits >> shift));
  }

  for (size_t offset = 0; offset < padded.size(); offset += 64) {
    std::array<uint32_t, 64> words = {};
    for (size_t i = 0; i < 16; ++i) {
      const size_t base = offset + i * 4;
      words[i] = (static_cast<uint32_t>(padded[base]) << 24) |
                 (static_cast<uint32_t>(padded[base + 1]) << 16) |
                 (static_cast<uint32_t>(padded[base + 2]) << 8) |
                 static_cast<uint32_t>(padded[base + 3]);
    }
    for (size_t i = 16; i < words.size(); ++i) {
      const uint32_t s0 = RotateRight(words[i - 15], 7) ^
                          RotateRight(words[i - 15], 18) ^
                          (words[i - 15] >> 3);
      const uint32_t s1 = RotateRight(words[i - 2], 17) ^
                          RotateRight(words[i - 2], 19) ^
                          (words[i - 2] >> 10);
      words[i] = words[i - 16] + s0 + words[i - 7] + s1;
    }

    uint32_t a = hash[0];
    uint32_t b = hash[1];
    uint32_t c = hash[2];
    uint32_t d = hash[3];
    uint32_t e = hash[4];
    uint32_t f = hash[5];
    uint32_t g = hash[6];
    uint32_t h = hash[7];
    for (size_t i = 0; i < words.size(); ++i) {
      const uint32_t sum1 = RotateRight(e, 6) ^ RotateRight(e, 11) ^
                            RotateRight(e, 25);
      const uint32_t choose = (e & f) ^ ((~e) & g);
      const uint32_t temp1 = h + sum1 + choose + kRoundConstants[i] + words[i];
      const uint32_t sum0 = RotateRight(a, 2) ^ RotateRight(a, 13) ^
                            RotateRight(a, 22);
      const uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
      const uint32_t temp2 = sum0 + majority;
      h = g;
      g = f;
      f = e;
      e = d + temp1;
      d = c;
      c = b;
      b = a;
      a = temp1 + temp2;
    }
    hash[0] += a;
    hash[1] += b;
    hash[2] += c;
    hash[3] += d;
    hash[4] += e;
    hash[5] += f;
    hash[6] += g;
    hash[7] += h;
  }

  static constexpr char kHex[] = "0123456789abcdef";
  std::string result;
  result.reserve(64);
  for (uint32_t word : hash) {
    for (int shift = 28; shift >= 0; shift -= 4) {
      result.push_back(kHex[(word >> shift) & 0x0f]);
    }
  }
  return result;
}

bool DecodeUtf8Scalar(absl::string_view value, size_t *offset,
                      uint32_t *codepoint) {
  const size_t begin = *offset;
  if (begin >= value.size()) {
    return false;
  }
  const uint8_t first = static_cast<uint8_t>(value[begin]);
  size_t length = 0;
  uint32_t scalar = 0;
  uint32_t minimum = 0;
  if (first <= 0x7f) {
    length = 1;
    scalar = first;
  } else if ((first & 0xe0) == 0xc0) {
    length = 2;
    scalar = first & 0x1f;
    minimum = 0x80;
  } else if ((first & 0xf0) == 0xe0) {
    length = 3;
    scalar = first & 0x0f;
    minimum = 0x800;
  } else if ((first & 0xf8) == 0xf0) {
    length = 4;
    scalar = first & 0x07;
    minimum = 0x10000;
  } else {
    return false;
  }
  if (begin + length > value.size()) {
    return false;
  }
  for (size_t i = 1; i < length; ++i) {
    const uint8_t continuation = static_cast<uint8_t>(value[begin + i]);
    if ((continuation & 0xc0) != 0x80) {
      return false;
    }
    scalar = (scalar << 6) | (continuation & 0x3f);
  }
  if (scalar < minimum || scalar > 0x10ffff ||
      (scalar >= 0xd800 && scalar <= 0xdfff)) {
    return false;
  }
  *offset += length;
  *codepoint = scalar;
  return true;
}

bool ValidText(absl::string_view value, size_t minimum, size_t maximum) {
  size_t offset = 0;
  size_t count = 0;
  while (offset < value.size()) {
    uint32_t scalar = 0;
    if (!DecodeUtf8Scalar(value, &offset, &scalar)) {
      return false;
    }
    if (scalar <= 0x1f || (scalar >= 0x7f && scalar <= 0x9f)) {
      return false;
    }
    ++count;
    if (count > maximum) {
      return false;
    }
  }
  return count >= minimum;
}

std::string TruncateUtf8(absl::string_view value, size_t maximum) {
  size_t offset = 0;
  size_t count = 0;
  while (offset < value.size() && count < maximum) {
    uint32_t ignored = 0;
    if (!DecodeUtf8Scalar(value, &offset, &ignored)) {
      return std::string();
    }
    ++count;
  }
  return std::string(value.substr(0, offset));
}

bool Digits(absl::string_view value, size_t begin, size_t end) {
  if (end > value.size()) {
    return false;
  }
  for (size_t i = begin; i < end; ++i) {
    if (value[i] < '0' || value[i] > '9') {
      return false;
    }
  }
  return true;
}

std::optional<int> Number(absl::string_view value, size_t begin, size_t end) {
  if (!Digits(value, begin, end)) {
    return std::nullopt;
  }
  int result = 0;
  for (size_t i = begin; i < end; ++i) {
    result = result * 10 + (value[i] - '0');
  }
  return result;
}

int DaysInMonth(int year, int month) {
  if (month == 2) {
    const bool leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    return leap ? 29 : 28;
  }
  if (month == 4 || month == 6 || month == 9 || month == 11) {
    return 30;
  }
  return 31;
}

bool ValidTimestamp(absl::string_view value) {
  if (value.size() < 20 || value.size() > ProtocolV1Limits::kTimestampBytes ||
      value[4] != '-' || value[7] != '-' || value[10] != 'T' ||
      value[13] != ':' || value[16] != ':' || !Digits(value, 0, 4) ||
      !Digits(value, 5, 7) || !Digits(value, 8, 10) ||
      !Digits(value, 11, 13) || !Digits(value, 14, 16) ||
      !Digits(value, 17, 19)) {
    return false;
  }
  for (const unsigned char byte : value) {
    if (byte > 0x7f) {
      return false;
    }
  }

  size_t zone = 19;
  if (value[zone] == '.') {
    const size_t fraction_begin = ++zone;
    while (zone < value.size() && value[zone] >= '0' && value[zone] <= '9') {
      ++zone;
    }
    if (zone - fraction_begin < 1 || zone - fraction_begin > 9) {
      return false;
    }
  }
  if (zone < value.size() && value[zone] == 'Z') {
    if (zone + 1 != value.size()) {
      return false;
    }
  } else {
    if (zone + 6 != value.size() ||
        (value[zone] != '+' && value[zone] != '-') || value[zone + 3] != ':' ||
        !Digits(value, zone + 1, zone + 3) ||
        !Digits(value, zone + 4, zone + 6) ||
        *Number(value, zone + 1, zone + 3) > 23 ||
        *Number(value, zone + 4, zone + 6) > 59) {
      return false;
    }
  }

  const int year = *Number(value, 0, 4);
  const int month = *Number(value, 5, 7);
  const int day = *Number(value, 8, 10);
  const int hour = *Number(value, 11, 13);
  const int minute = *Number(value, 14, 16);
  const int second = *Number(value, 17, 19);
  return month >= 1 && month <= 12 && day >= 1 &&
         day <= DaysInMonth(year, month) && hour <= 23 && minute <= 59 &&
         second <= 59;
}

template <typename Message>
bool ParseJson(absl::string_view bytes, Message *message) {
  google::protobuf::util::JsonParseOptions options;
  options.ignore_unknown_fields = true;
  const absl::Status status =
      google::protobuf::util::JsonStringToMessage(bytes, message, options);
  return status.ok() && message->IsInitialized();
}

bool HasJsonField(absl::string_view bytes, absl::string_view field) {
  google::protobuf::Struct object;
  if (!ParseJson(bytes, &object)) {
    return false;
  }
  return object.fields().contains(std::string(field));
}

bool NullableString(const google::protobuf::Value &value,
                    std::optional<std::string> *result) {
  switch (value.kind_case()) {
    case google::protobuf::Value::kNullValue:
      *result = std::nullopt;
      return true;
    case google::protobuf::Value::kStringValue:
      *result = value.string_value();
      return true;
    default:
      return false;
  }
}

bool ValidateState(const protocol_v1::StateJson &state,
                   std::optional<std::string> *project_id) {
  if (state.format_version() != 1 || !ValidTimestamp(state.updated_at()) ||
      !NullableString(state.active_project_id(), project_id)) {
    return false;
  }
  return !*project_id || IsSafeIdentifier(**project_id,
                                          ProtocolV1Limits::kProjectIdScalars);
}

std::optional<DictionaryCategory> ParseCategory(absl::string_view category) {
  if (category == "person") {
    return DictionaryCategory::kPerson;
  }
  if (category == "place") {
    return DictionaryCategory::kPlace;
  }
  if (category == "noun") {
    return DictionaryCategory::kNoun;
  }
  return std::nullopt;
}

bool ValidateProject(const protocol_v1::ProjectJson &project,
                     absl::string_view expected_project_id,
                     std::vector<DictionaryEntryDto> *entries,
                     ProjectConditionsDto *conditions) {
  if (project.format_version() != 1 ||
      project.project_id() != expected_project_id ||
      !IsSafeIdentifier(project.project_id(),
                        ProtocolV1Limits::kProjectIdScalars) ||
      !ValidText(project.project_name(), 1,
                 ProtocolV1Limits::kProjectNameScalars) ||
      !ValidTimestamp(project.generated_at()) ||
      static_cast<size_t>(project.entries_size()) >
          ProtocolV1Limits::kProjectEntries) {
    return false;
  }
  if (project.has_profile() &&
      !ValidText(project.profile(), 0, ProtocolV1Limits::kProfileScalars)) {
    return false;
  }

  entries->clear();
  entries->reserve(project.entries_size());
  for (const protocol_v1::DictionaryEntryJson &entry : project.entries()) {
    const std::optional<DictionaryCategory> category =
        ParseCategory(entry.category());
    if (!category ||
        !ValidText(entry.yomi(), 1, ProtocolV1Limits::kEntryYomiScalars) ||
        !ValidText(entry.surface(), 1,
                   ProtocolV1Limits::kEntrySurfaceScalars) ||
        entry.priority() < 1 || entry.priority() > 3 ||
        !IsSafeIdentifier(entry.entry_id(),
                          ProtocolV1Limits::kEntryIdScalars)) {
      return false;
    }
    entries->push_back(DictionaryEntryDto{
        .yomi = entry.yomi(),
        .surface = entry.surface(),
        .category = *category,
        .priority = entry.priority(),
        .entry_id = entry.entry_id(),
    });
  }

  *conditions = ProjectConditionsDto();
  if (project.has_zenzai_context()) {
    const protocol_v1::ZenzaiContextJson &context = project.zenzai_context();
    std::optional<std::string> style;
    std::optional<std::string> preference;
    if (!ValidText(context.topic(), 1,
                   ProtocolV1Limits::kZenzaiConditionScalars) ||
        !NullableString(context.style(), &style) ||
        !NullableString(context.preference(), &preference)) {
      return false;
    }
    for (const std::optional<std::string> *value : {&style, &preference}) {
      if (*value && !ValidText(**value, 0,
                              ProtocolV1Limits::kZenzaiConditionScalars)) {
        return false;
      }
    }
    conditions->topic = TruncateUtf8(
        context.topic(), ProtocolV1Limits::kConverterConditionScalars);
    if (style) {
      conditions->style = TruncateUtf8(
          *style, ProtocolV1Limits::kConverterConditionScalars);
    }
    if (preference) {
      conditions->preference = TruncateUtf8(
          *preference, ProtocolV1Limits::kConverterConditionScalars);
    }
  } else if (project.has_profile() && !project.profile().empty()) {
    conditions->topic = TruncateUtf8(
        project.profile(), ProtocolV1Limits::kConverterConditionScalars);
  }
  return true;
}

}  // namespace

bool ProtocolV1Snapshot::SemanticallyEquals(
    const ProtocolV1Snapshot &other) const {
  return project_id == other.project_id && project_name == other.project_name &&
         entries == other.entries && conditions == other.conditions;
}

bool LoadResult::IsRetryable() const {
  return diagnostic == LoadDiagnostic::kMissingSnapshot ||
         diagnostic == LoadDiagnostic::kStateChangedDuringRead;
}

VerifiedFileBytes VerifiedFileBytes::FromBytes(std::string bytes) {
  const std::string digest = Sha256(bytes);
  return VerifiedFileBytes{.bytes = std::move(bytes), .sha256 = digest};
}

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

ProtocolV1Loader::ProtocolV1Loader(
    std::shared_ptr<ProtocolV1FileReader> reader)
    : ProtocolV1Loader(std::move(reader), Options()) {}

ProtocolV1Loader::ProtocolV1Loader(
    std::shared_ptr<ProtocolV1FileReader> reader, Options options)
    : reader_(std::move(reader)), options_(options) {}

LoadResult ProtocolV1Loader::Load() {
  LoadResult result;
  for (size_t attempt = 0; attempt <= options_.stale_retries; ++attempt) {
    result = LoadOnce();
    if (!result.IsRetryable()) {
      break;
    }
  }
  return result;
}

LoadResult ProtocolV1Loader::LoadOnce() {
  absl::StatusOr<VerifiedFileBytes> first_state =
      reader_->ReadState(ProtocolV1Limits::kStateBytes);
  if (!first_state.ok()) {
    return LoadResult{
        .diagnostic = absl::IsNotFound(first_state.status())
                          ? LoadDiagnostic::kMissingState
                          : LoadDiagnostic::kInvalidState,
    };
  }

  protocol_v1::StateJson state;
  std::optional<std::string> project_id;
  if (!HasJsonField(first_state->bytes, "active_project_id") ||
      !ParseJson(first_state->bytes, &state) ||
      !ValidateState(state, &project_id)) {
    return LoadResult{.diagnostic = LoadDiagnostic::kInvalidState};
  }
  if (!project_id) {
    return LoadResult{.diagnostic = LoadDiagnostic::kInactive};
  }

  absl::StatusOr<VerifiedFileBytes> project_file =
      reader_->ReadProject(*project_id, ProtocolV1Limits::kProjectBytes);
  if (!project_file.ok()) {
    return LoadResult{
        .diagnostic = absl::IsNotFound(project_file.status())
                          ? LoadDiagnostic::kMissingSnapshot
                          : LoadDiagnostic::kInvalidSnapshot,
    };
  }
  protocol_v1::ProjectJson project;
  std::vector<DictionaryEntryDto> entries;
  ProjectConditionsDto conditions;
  if (!HasJsonField(project_file->bytes, "entries") ||
      !ParseJson(project_file->bytes, &project) ||
      !ValidateProject(project, *project_id, &entries, &conditions)) {
    return LoadResult{.diagnostic = LoadDiagnostic::kInvalidSnapshot};
  }

  absl::StatusOr<VerifiedFileBytes> second_state =
      reader_->ReadState(ProtocolV1Limits::kStateBytes);
  if (!second_state.ok()) {
    return LoadResult{
        .diagnostic = LoadDiagnostic::kStateChangedDuringRead,
    };
  }
  protocol_v1::StateJson checked_second_state;
  std::optional<std::string> second_project_id;
  if (!HasJsonField(second_state->bytes, "active_project_id") ||
      !ParseJson(second_state->bytes, &checked_second_state) ||
      !ValidateState(checked_second_state, &second_project_id) ||
      second_state->bytes != first_state->bytes ||
      second_state->sha256 != first_state->sha256) {
    return LoadResult{
        .diagnostic = LoadDiagnostic::kStateChangedDuringRead,
    };
  }

  auto snapshot = std::make_shared<const ProtocolV1Snapshot>(
      ProtocolV1Snapshot{
          .project_id = project.project_id(),
          .project_name = project.project_name(),
          .entries = std::move(entries),
          .conditions = std::move(conditions),
          .state_updated_at = state.updated_at(),
          .project_generated_at = project.generated_at(),
          .state_sha256 = first_state->sha256,
          .project_sha256 = project_file->sha256,
      });
  return LoadResult{
      .snapshot = std::move(snapshot),
      .diagnostic = LoadDiagnostic::kLoaded,
  };
}

ProtocolV1SnapshotPublisher::ProtocolV1SnapshotPublisher(
    std::shared_ptr<ProtocolV1Loader> loader)
    : loader_(std::move(loader)),
      published_(std::make_shared<const PublishedProtocolV1Snapshot>()) {}

std::shared_ptr<const PublishedProtocolV1Snapshot>
ProtocolV1SnapshotPublisher::Reload() {
  std::lock_guard<std::mutex> reload_guard(reload_mutex_);
  const LoadResult result = loader_->Load();
  std::lock_guard<std::mutex> published_guard(published_mutex_);

  if (result.IsRetryable()) {
    ++consecutive_retryable_failures_;
    if (consecutive_retryable_failures_ == 1) {
      published_ = std::make_shared<const PublishedProtocolV1Snapshot>(
          PublishedProtocolV1Snapshot{
              .sequence = published_->sequence,
              .snapshot = published_->snapshot,
              .diagnostic = result.diagnostic,
          });
      return published_;
    }
  } else {
    consecutive_retryable_failures_ = 0;
  }

  bool semantic_change = false;
  if (static_cast<bool>(published_->snapshot) !=
      static_cast<bool>(result.snapshot)) {
    semantic_change = true;
  } else if (published_->snapshot && result.snapshot &&
             !published_->snapshot->SemanticallyEquals(*result.snapshot)) {
    semantic_change = true;
  }
  uint64_t sequence = published_->sequence;
  if (semantic_change && sequence != std::numeric_limits<uint64_t>::max()) {
    ++sequence;
  }
  published_ = std::make_shared<const PublishedProtocolV1Snapshot>(
      PublishedProtocolV1Snapshot{
          .sequence = sequence,
          .snapshot = result.snapshot,
          .diagnostic = result.diagnostic,
      });
  return published_;
}

std::shared_ptr<const PublishedProtocolV1Snapshot>
ProtocolV1SnapshotPublisher::Latest() const {
  std::lock_guard<std::mutex> guard(published_mutex_);
  return published_;
}

}  // namespace mozc::grimodex
