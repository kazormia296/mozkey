#include "session/zenz_context_sanitizer.h"

#include <cstddef>
#include <string>

#include "absl/strings/ascii.h"
#include "absl/strings/match.h"
#include "absl/strings/string_view.h"
#include "base/util.h"

namespace mozc {
namespace session {
namespace {

bool IsAsciiAlphaNum(unsigned char c) {
  return absl::ascii_isalnum(c);
}

bool IsAsciiDigit(unsigned char c) {
  return absl::ascii_isdigit(c);
}

bool IsAsciiVisible(unsigned char c) {
  return 0x21 <= c && c <= 0x7e;
}

bool IsLikelyJapaneseUtf8Lead(unsigned char c) {
  // Hiragana/Katakana/CJK are multi-byte in UTF-8.  This deliberately treats
  // all non-ASCII bytes as Japanese-like for a cheap first-pass classifier.
  // Sensitive ASCII patterns are rejected separately.
  return c >= 0x80;
}

std::string TruncateRightByChars(absl::string_view text, size_t max_chars) {
  if (max_chars == 0 || text.empty()) {
    return "";
  }

  const size_t len = Util::CharsLen(text);
  if (len <= max_chars) {
    return std::string(text);
  }

  return std::string(Util::Utf8SubString(text, len - max_chars, max_chars));
}

}  // namespace

bool ZenzContextSanitizer::ContainsLongAsciiRun(absl::string_view text) {
  size_t run = 0;
  for (const unsigned char c : text) {
    if (IsAsciiVisible(c)) {
      ++run;
      if (run >= 8) {
        return true;
      }
    } else {
      run = 0;
    }
  }
  return false;
}

bool ZenzContextSanitizer::ContainsLongDigitRun(absl::string_view text) {
  size_t run = 0;
  for (const unsigned char c : text) {
    if (IsAsciiDigit(c)) {
      ++run;
      if (run >= 4) {
        return true;
      }
    } else {
      run = 0;
    }
  }
  return false;
}

bool ZenzContextSanitizer::ContainsSensitiveAsciiPattern(
    absl::string_view text) {
  const std::string lower = absl::AsciiStrToLower(std::string(text));

  if (absl::StrContains(lower, "password") ||
      absl::StrContains(lower, "passwd") ||
      absl::StrContains(lower, "pwd") ||
      absl::StrContains(lower, "token") ||
      absl::StrContains(lower, "secret") ||
      absl::StrContains(lower, "apikey") ||
      absl::StrContains(lower, "api_key") ||
      absl::StrContains(lower, "authorization") ||
      absl::StrContains(lower, "bearer ") ||
      absl::StrContains(lower, "cookie") ||
      absl::StrContains(lower, "sessionid") ||
      absl::StrContains(lower, "session_id")) {
    return true;
  }

  if (absl::StrContains(lower, "http://") ||
      absl::StrContains(lower, "https://") ||
      absl::StrContains(lower, "www.") ||
      absl::StrContains(lower, "mailto:")) {
    return true;
  }

  if (absl::StrContains(lower, "@") &&
      absl::StrContains(lower, ".")) {
    return true;
  }

  if (absl::StrContains(lower, "c:\\") ||
      absl::StrContains(lower, "\\users\\") ||
      absl::StrContains(lower, "/home/") ||
      absl::StrContains(lower, "/users/")) {
    return true;
  }

  if (absl::StrContains(lower, "sk-") ||
      absl::StrContains(lower, "pk_") ||
      absl::StrContains(lower, "ghp_") ||
      absl::StrContains(lower, "xoxb-")) {
    return true;
  }

  return ContainsLongAsciiRun(text) || ContainsLongDigitRun(text);
}

bool ZenzContextSanitizer::LooksMostlyJapaneseContext(absl::string_view text) {
  if (text.empty()) {
    return false;
  }

  size_t non_ascii = 0;
  size_t ascii_alnum = 0;
  size_t ascii_visible = 0;

  for (const unsigned char c : text) {
    if (IsLikelyJapaneseUtf8Lead(c)) {
      ++non_ascii;
      continue;
    }

    if (IsAsciiAlphaNum(c)) {
      ++ascii_alnum;
    }

    if (IsAsciiVisible(c)) {
      ++ascii_visible;
    }
  }

  // Conservative rule:
  // - Japanese-like bytes must dominate.
  // - ASCII alnum should be small.
  // - visible ASCII should not dominate the string.
  return non_ascii > 0 &&
         non_ascii >= ascii_alnum * 2 &&
         ascii_alnum <= 4 &&
         ascii_visible <= non_ascii;
}

std::string ZenzContextSanitizer::Classify(absl::string_view text) {
  if (text.empty()) {
    return "empty";
  }

  if (ContainsSensitiveAsciiPattern(text)) {
    return "sensitive_like";
  }

  bool has_non_ascii = false;
  bool has_ascii_alnum = false;
  bool has_digit = false;
  bool has_symbol = false;

  for (const unsigned char c : text) {
    if (IsLikelyJapaneseUtf8Lead(c)) {
      has_non_ascii = true;
    } else if (absl::ascii_isdigit(c)) {
      has_ascii_alnum = true;
      has_digit = true;
    } else if (absl::ascii_isalpha(c)) {
      has_ascii_alnum = true;
    } else if (IsAsciiVisible(c)) {
      has_symbol = true;
    }
  }

  if (has_non_ascii && !has_ascii_alnum && !has_symbol) {
    return "japanese_only";
  }

  if (has_non_ascii && !has_ascii_alnum) {
    return "japanese_with_punctuation";
  }

  if (has_non_ascii && has_ascii_alnum) {
    return "mixed_japanese_ascii";
  }

  if (has_ascii_alnum || has_digit) {
    return "ascii_or_digit";
  }

  return "symbol_or_other";
}

ZenzContextSanitizationResult ZenzContextSanitizer::SanitizeForZenz(
    absl::string_view raw_context,
    size_t max_chars) const {
  ZenzContextSanitizationResult result;

  if (raw_context.empty() || max_chars == 0) {
    result.context_class = "empty";
    result.reason = "empty_context";
    return result;
  }

  const std::string truncated = TruncateRightByChars(raw_context, max_chars);
  result.context_class = Classify(truncated);

  // Never allow raw context to be persisted.  Learning may use context_class
  // only, which is non-reversible.
  result.allowed_for_learning = false;

  if (result.context_class == "sensitive_like") {
    result.reason = "sensitive_context_rejected";
    return result;
  }

  if (!LooksMostlyJapaneseContext(truncated)) {
    result.reason = "non_japanese_context_rejected";
    return result;
  }

  result.sanitized_context = truncated;
  result.allowed_for_prompt = true;
  result.reason = "context_allowed";
  return result;
}

}  // namespace session
}  // namespace mozc
