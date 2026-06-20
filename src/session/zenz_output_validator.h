#ifndef MOZC_SESSION_ZENZ_OUTPUT_VALIDATOR_H_
#define MOZC_SESSION_ZENZ_OUTPUT_VALIDATOR_H_

#include <cstdint>
#include <string>

#include "absl/strings/string_view.h"

namespace mozc {
namespace session {

struct ZenzValidationInput {
  std::string key;
  std::string mozc_value;
  std::string zenz_value;
  std::string left_context;

  uint32_t min_key_length = 5;
  bool allow_synthetic_candidate = true;
};

struct ZenzValidationResult {
  bool accept = false;
  bool synthetic = false;
  std::string reason;
};

class ZenzOutputValidator {
 public:
  ZenzValidationResult Validate(const ZenzValidationInput& input) const;

  // Restores user-visible symbol style that Zenz may normalize. This preserves
  // the user's current composition style instead of normalizing to either
  // fullwidth or ASCII: if the key or original Mozc value used Japanese-width
  // punctuation, ASCII-normalized Zenz punctuation is restored; if the source
  // used ASCII punctuation, fullwidth Zenz punctuation is restored to ASCII.
  // The returned value is used before validation, display, commit, and
  // feedback recording.
  static std::string RestoreUserVisibleSymbolStyle(
      absl::string_view key,
      absl::string_view mozc_value,
      absl::string_view zenz_value);

 private:
  static bool ContainsSpecialToken(absl::string_view text);
  static bool LooksLikeUrlOrEmail(absl::string_view text);
  static bool LooksLikeSecret(absl::string_view text);
};

}  // namespace session
}  // namespace mozc

#endif  // MOZC_SESSION_ZENZ_OUTPUT_VALIDATOR_H_
