#include "session/zenz_output_validator.h"

#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>

#include "absl/strings/match.h"
#include "base/util.h"

namespace mozc {
namespace session {
namespace {

ZenzValidationResult Accept(bool synthetic, std::string reason) {
  ZenzValidationResult result;
  result.accept = true;
  result.synthetic = synthetic;
  result.reason = std::move(reason);
  return result;
}

ZenzValidationResult Reject(std::string reason) {
  ZenzValidationResult result;
  result.accept = false;
  result.synthetic = false;
  result.reason = std::move(reason);
  return result;
}

bool DecodeOneUtf8(absl::string_view input, size_t* index, char32_t* cp) {
  if (*index >= input.size()) {
    return false;
  }

  const unsigned char c0 = static_cast<unsigned char>(input[*index]);

  if (c0 < 0x80) {
    *cp = c0;
    ++(*index);
    return true;
  }

  if ((c0 & 0xE0) == 0xC0) {
    if (*index + 1 >= input.size()) return false;
    const unsigned char c1 = static_cast<unsigned char>(input[*index + 1]);
    if ((c1 & 0xC0) != 0x80) return false;
    *cp = ((c0 & 0x1F) << 6) | (c1 & 0x3F);
    *index += 2;
    return true;
  }

  if ((c0 & 0xF0) == 0xE0) {
    if (*index + 2 >= input.size()) return false;
    const unsigned char c1 = static_cast<unsigned char>(input[*index + 1]);
    const unsigned char c2 = static_cast<unsigned char>(input[*index + 2]);
    if ((c1 & 0xC0) != 0x80 || (c2 & 0xC0) != 0x80) return false;
    *cp = ((c0 & 0x0F) << 12) |
          ((c1 & 0x3F) << 6) |
          (c2 & 0x3F);
    *index += 3;
    return true;
  }

  if ((c0 & 0xF8) == 0xF0) {
    if (*index + 3 >= input.size()) return false;
    const unsigned char c1 = static_cast<unsigned char>(input[*index + 1]);
    const unsigned char c2 = static_cast<unsigned char>(input[*index + 2]);
    const unsigned char c3 = static_cast<unsigned char>(input[*index + 3]);
    if ((c1 & 0xC0) != 0x80 ||
        (c2 & 0xC0) != 0x80 ||
        (c3 & 0xC0) != 0x80) {
      return false;
    }
    *cp = ((c0 & 0x07) << 18) |
          ((c1 & 0x3F) << 12) |
          ((c2 & 0x3F) << 6) |
          (c3 & 0x3F);
    *index += 4;
    return true;
  }

  return false;
}

}  // namespace

bool ZenzOutputValidator::ContainsSpecialToken(absl::string_view text) {
  if (absl::StrContains(text, "<s>") ||
      absl::StrContains(text, "</s>") ||
      absl::StrContains(text, "<unk>") ||
      absl::StrContains(text, "<|endoftext|>")) {
    return true;
  }

  size_t index = 0;
  while (index < text.size()) {
    char32_t cp = 0;
    if (!DecodeOneUtf8(text, &index, &cp)) {
      return true;
    }

    // zenz prompt private-use markers: U+EE00..U+EE06.
    if (0xEE00 <= cp && cp <= 0xEE06) {
      return true;
    }
  }

  return false;
}

bool ZenzOutputValidator::LooksLikeUrlOrEmail(absl::string_view text) {
  return absl::StrContains(text, "://") ||
         absl::StrContains(text, "www.") ||
         absl::StrContains(text, "@");
}

bool ZenzOutputValidator::LooksLikeSecret(absl::string_view text) {
  const size_t len = text.size();

  // Long ASCII-ish strings are often IDs, tokens, hashes, or keys.
  if (len >= 32) {
    size_t ascii_alnum = 0;
    for (unsigned char c : text) {
      if (('a' <= c && c <= 'z') ||
          ('A' <= c && c <= 'Z') ||
          ('0' <= c && c <= '9') ||
          c == '_' || c == '-' || c == '.') {
        ++ascii_alnum;
      }
    }
    if (ascii_alnum * 100 / len >= 80) {
      return true;
    }
  }

  return absl::StrContains(text, "password") ||
         absl::StrContains(text, "passwd") ||
         absl::StrContains(text, "secret") ||
         absl::StrContains(text, "token") ||
         absl::StrContains(text, "api_key") ||
         absl::StrContains(text, "apikey");
}

ZenzValidationResult ZenzOutputValidator::Validate(
    const ZenzValidationInput& input) const {
  if (input.key.empty()) {
    return Reject("empty_key");
  }

  if (input.zenz_value.empty()) {
    return Reject("empty_value");
  }

  if (input.zenz_value == input.mozc_value) {
    return Reject("same_as_mozc");
  }

  if (Util::CharsLen(input.key) < input.min_key_length) {
    return Reject("too_short_key");
  }

  if (ContainsSpecialToken(input.zenz_value)) {
    return Reject("special_token");
  }

  if (!Util::IsValidUtf8(input.zenz_value)) {
    return Reject("invalid_utf8");
  }

  const size_t key_len = Util::CharsLen(input.key);
  const size_t value_len = Util::CharsLen(input.zenz_value);

  if (value_len == 0) {
    return Reject("zero_value_len");
  }

  // Conservative length guard. Japanese conversion can shrink/expand, but not
  // arbitrarily.
  if (value_len > key_len * 3 + 8) {
    return Reject("too_long_value");
  }

  if (LooksLikeUrlOrEmail(input.zenz_value)) {
    return Reject("url_or_email");
  }

  if (LooksLikeSecret(input.zenz_value)) {
    return Reject("secret_like");
  }

  if (!input.allow_synthetic_candidate) {
    return Reject("synthetic_disabled");
  }

  return Accept(/*synthetic=*/true, "accepted_synthetic");
}

}  // namespace session
}  // namespace mozc
