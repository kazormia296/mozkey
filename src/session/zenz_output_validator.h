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

 private:
  static bool ContainsSpecialToken(absl::string_view text);
  static bool LooksLikeUrlOrEmail(absl::string_view text);
  static bool LooksLikeSecret(absl::string_view text);
};

}  // namespace session
}  // namespace mozc

#endif  // MOZC_SESSION_ZENZ_OUTPUT_VALIDATOR_H_
