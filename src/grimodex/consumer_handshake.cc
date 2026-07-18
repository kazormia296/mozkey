// Copyright 2026 The Mozkey Authors

#include "grimodex/consumer_handshake.h"

#include <cstddef>
#include <cstdint>
#include <string>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/ascii.h"
#include "absl/strings/string_view.h"
#include "absl/time/time.h"

namespace mozc::grimodex {
namespace {

constexpr size_t kMaxConsumerIdBytes = 64;
constexpr size_t kMaxNameBytes = 256;
constexpr size_t kMaxVersionBytes = 64;
constexpr size_t kMaxPlatformBytes = 32;
constexpr size_t kMaxTimestampBytes = 64;

bool IsAsciiLower(unsigned char value) {
  return value >= 'a' && value <= 'z';
}

bool IsAsciiDigit(unsigned char value) {
  return value >= '0' && value <= '9';
}

bool IsSafeConsumerId(absl::string_view value) {
  if (value.empty() || value.size() > kMaxConsumerIdBytes) {
    return false;
  }
  const auto is_alphanumeric = [](unsigned char byte) {
    return IsAsciiLower(byte) || IsAsciiDigit(byte);
  };
  if (!is_alphanumeric(value.front()) || !is_alphanumeric(value.back())) {
    return false;
  }
  for (const unsigned char byte : value) {
    if (!(is_alphanumeric(byte) || byte == '-' || byte == '_' ||
          byte == '.')) {
      return false;
    }
  }
  return true;
}

bool IsSafeVersion(absl::string_view value) {
  if (value.empty() || value.size() > kMaxVersionBytes) {
    return false;
  }
  for (const unsigned char byte : value) {
    if (!(absl::ascii_isalnum(byte) || byte == '.' || byte == '-' ||
          byte == '_' || byte == '+')) {
      return false;
    }
  }
  return true;
}

bool IsSafePlatform(absl::string_view value) {
  if (value.empty() || value.size() > kMaxPlatformBytes) {
    return false;
  }
  return value == "linux" || value == "windows" || value == "macos";
}

bool IsValidTimestamp(absl::string_view value) {
  if (value.empty() || value.size() > kMaxTimestampBytes ||
      value.back() != 'Z') {
    return false;
  }
  absl::Time parsed;
  std::string error;
  return absl::ParseTime(absl::RFC3339_full, value, &parsed, &error);
}

bool IsContinuation(unsigned char value) {
  return value >= 0x80 && value <= 0xbf;
}

// JSON text is UTF-8.  Reject malformed, overlong, surrogate, and out-of-range
// sequences before serializing rather than emitting JSON that different
// parsers could interpret inconsistently.
bool IsValidUtf8(absl::string_view value) {
  size_t index = 0;
  while (index < value.size()) {
    const unsigned char first = value[index];
    if (first <= 0x7f) {
      ++index;
      continue;
    }
    if (first >= 0xc2 && first <= 0xdf) {
      if (index + 1 >= value.size() ||
          !IsContinuation(value[index + 1])) {
        return false;
      }
      index += 2;
      continue;
    }
    if (first >= 0xe0 && first <= 0xef) {
      if (index + 2 >= value.size()) {
        return false;
      }
      const unsigned char second = value[index + 1];
      const unsigned char third = value[index + 2];
      if (!IsContinuation(third) ||
          (first == 0xe0 && (second < 0xa0 || second > 0xbf)) ||
          (first == 0xed && (second < 0x80 || second > 0x9f)) ||
          (first != 0xe0 && first != 0xed && !IsContinuation(second))) {
        return false;
      }
      index += 3;
      continue;
    }
    if (first >= 0xf0 && first <= 0xf4) {
      if (index + 3 >= value.size()) {
        return false;
      }
      const unsigned char second = value[index + 1];
      const unsigned char third = value[index + 2];
      const unsigned char fourth = value[index + 3];
      if (!IsContinuation(third) || !IsContinuation(fourth) ||
          (first == 0xf0 && (second < 0x90 || second > 0xbf)) ||
          (first == 0xf4 && (second < 0x80 || second > 0x8f)) ||
          (first != 0xf0 && first != 0xf4 && !IsContinuation(second))) {
        return false;
      }
      index += 4;
      continue;
    }
    return false;
  }
  return true;
}

void AppendJsonString(absl::string_view value, std::string *output) {
  constexpr char kHex[] = "0123456789abcdef";
  output->push_back('"');
  for (const unsigned char byte : value) {
    switch (byte) {
      case '"':
        output->append("\\\"");
        break;
      case '\\':
        output->append("\\\\");
        break;
      case '\b':
        output->append("\\b");
        break;
      case '\f':
        output->append("\\f");
        break;
      case '\n':
        output->append("\\n");
        break;
      case '\r':
        output->append("\\r");
        break;
      case '\t':
        output->append("\\t");
        break;
      default:
        if (byte < 0x20) {
          output->append("\\u00");
          output->push_back(kHex[(byte >> 4) & 0x0f]);
          output->push_back(kHex[byte & 0x0f]);
        } else {
          output->push_back(static_cast<char>(byte));
        }
    }
  }
  output->push_back('"');
}

void AppendBoolean(bool value, std::string *output) {
  output->append(value ? "true" : "false");
}

}  // namespace

absl::Status ValidateConsumerHandshake(const ConsumerHandshake &handshake) {
  if (!IsSafeConsumerId(handshake.consumer_id)) {
    return absl::InvalidArgumentError("invalid Grimodex consumer ID");
  }
  if (handshake.name.empty() || handshake.name.size() > kMaxNameBytes ||
      absl::StripAsciiWhitespace(handshake.name).empty() ||
      !IsValidUtf8(handshake.name)) {
    return absl::InvalidArgumentError("invalid Grimodex consumer name");
  }
  if (!IsSafeVersion(handshake.version)) {
    return absl::InvalidArgumentError("invalid Grimodex consumer version");
  }
  if (!IsSafePlatform(handshake.platform)) {
    return absl::InvalidArgumentError("invalid Grimodex consumer platform");
  }
  if (!IsValidTimestamp(handshake.last_seen)) {
    return absl::InvalidArgumentError("invalid Grimodex consumer timestamp");
  }
  return absl::OkStatus();
}

absl::StatusOr<std::string> SerializeConsumerHandshake(
    const ConsumerHandshake &handshake) {
  if (absl::Status status = ValidateConsumerHandshake(handshake);
      !status.ok()) {
    return status;
  }

  std::string output;
  output.reserve(512);
  output.append(R"json({"capabilities":{"application_scoping":)json");
  AppendBoolean(handshake.capabilities.application_scoping, &output);
  output.append(R"json(,"dynamic_dictionary":)json");
  AppendBoolean(handshake.capabilities.dynamic_dictionary, &output);
  output.append(R"json(,"profile":)json");
  AppendBoolean(handshake.capabilities.profile, &output);
  output.append(R"json(,"zenzai_v3_conditions":)json");
  AppendBoolean(handshake.capabilities.zenzai_v3_conditions, &output);
  output.append(R"json(},"consumer_id":)json");
  AppendJsonString(handshake.consumer_id, &output);
  output.append(R"json(,"format_version":1,"last_seen":)json");
  AppendJsonString(handshake.last_seen, &output);
  output.append(R"json(,"name":)json");
  AppendJsonString(handshake.name, &output);
  output.append(R"json(,"platform":)json");
  AppendJsonString(handshake.platform, &output);
  output.append(R"json(,"version":)json");
  AppendJsonString(handshake.version, &output);
  output.append("}\n");

  if (output.size() > kMaxConsumerHandshakeBytes) {
    return absl::ResourceExhaustedError(
        "Grimodex consumer handshake exceeds its wire limit");
  }
  return output;
}

bool ShouldRefresh(absl::Time last_success, absl::Time now) {
  return now < last_success ||
         now - last_success >= kConsumerRefreshInterval;
}

}  // namespace mozc::grimodex
