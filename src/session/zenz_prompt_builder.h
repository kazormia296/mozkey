#ifndef MOZC_SESSION_ZENZ_PROMPT_BUILDER_H_
#define MOZC_SESSION_ZENZ_PROMPT_BUILDER_H_

#include <string>

#include "absl/strings/string_view.h"

namespace mozc {
namespace session {

class ZenzPromptBuilder {
 public:
  ZenzPromptBuilder() = default;

  std::string HiraganaToKatakana(absl::string_view input) const;

  std::string Build(absl::string_view left_context,
                    absl::string_view reading_hiragana) const;

 private:
  static bool DecodeOneUtf8(absl::string_view input, size_t* index,
                            char32_t* codepoint);
  static void AppendUtf8(char32_t codepoint, std::string* output);
};

}  // namespace session
}  // namespace mozc

#endif  // MOZC_SESSION_ZENZ_PROMPT_BUILDER_H_
