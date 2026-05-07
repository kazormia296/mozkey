// Copyright 2010-2021, Google Inc.
// All rights reserved.
//
// Redistribution and use in source and binary forms, with or without
// modification, are permitted provided that the following conditions are
// met:
//
//     * Redistributions of source code must retain the above copyright
// notice, this list of conditions and the following disclaimer.
//     * Redistributions in binary form must reproduce the above
// copyright notice, this list of conditions and the following disclaimer
// in the documentation and/or other materials provided with the
// distribution.
//     * Neither the name of Google Inc. nor the names of its
// contributors may be used to endorse or promote products derived from
// this software without specific prior written permission.
//
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
// "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
// LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
// A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
// OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
// SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
// LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
// DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
// THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
// (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
// OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

#include "converter/history_reconstructor.h"

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "absl/strings/string_view.h"
#include "base/japanese_util.h"
#include "base/util.h"
#include "converter/attribute.h"
#include "converter/candidate.h"
#include "converter/segments.h"
#include "dictionary/pos_matcher.h"
#include "protocol/commands.pb.h"

namespace mozc {
namespace converter {
namespace {

// Characters that should break left-context reconstruction.
// If preceding_text ends with one of these, we should not connect it to the
// next composition.
bool IsHardBoundary(const char32_t codepoint) {
  switch (codepoint) {
    case U' ':
    case U'　':
    case U'\t':
    case U'\r':
    case U'\n':
    case U'.':
    case U',':
    case U'!':
    case U'?':
    case U';':
    case U':':
    case U'。':
    case U'、':
    case U'．':
    case U'，':
    case U'！':
    case U'？':
    case U'；':
    case U'：':
    case U'・':
    case U'「':
    case U'」':
    case U'『':
    case U'』':
    case U'（':
    case U'）':
    case U'(':
    case U')':
    case U'[':
    case U']':
    case U'［':
    case U'］':
    case U'{':
    case U'}':
    case U'｛':
    case U'｝':
    case U'“':
    case U'”':
    case U'"':
    case U'\'':
      return true;
    default:
      return false;
  }
}

// Japanese noun-like symbols that are often used in names/placeholders.
// U+3007 "〇" is important for strings such as "〇〇".
bool IsJapaneseTokenSymbol(const char32_t codepoint) {
  switch (codepoint) {
    case U'〇':
    case U'々':
    case U'〆':
    case U'ヶ':
    case U'ヵ':
    case U'ー':
      return true;
    default:
      return false;
  }
}

bool IsJapaneseScriptType(const Util::ScriptType script_type) {
  switch (script_type) {
    case Util::KANJI:
    case Util::HIRAGANA:
    case Util::KATAKANA:
      return true;
    default:
      return false;
  }
}

// Inside a Japanese-looking tail chunk, allow ASCII/number too, so that
// "Issue番号" can be reconstructed as one history chunk.
bool IsJapaneseChunkChar(const char32_t codepoint) {
  if (IsJapaneseTokenSymbol(codepoint)) {
    return true;
  }

  switch (Util::GetScriptType(codepoint)) {
    case Util::KANJI:
    case Util::HIRAGANA:
    case Util::KATAKANA:
    case Util::NUMBER:
    case Util::ALPHABET:
      return true;
    default:
      return false;
  }
}

Util::ScriptType ClassifyJapaneseChunk(
    const std::vector<char32_t>& reverse_token) {
  bool has_kanji = false;
  bool has_katakana = false;
  bool has_hiragana = false;
  bool has_japanese_symbol = false;

  for (const char32_t codepoint : reverse_token) {
    if (IsJapaneseTokenSymbol(codepoint)) {
      has_japanese_symbol = true;
      continue;
    }

    switch (Util::GetScriptType(codepoint)) {
      case Util::KANJI:
        has_kanji = true;
        break;
      case Util::KATAKANA:
        has_katakana = true;
        break;
      case Util::HIRAGANA:
        has_hiragana = true;
        break;
      default:
        break;
    }
  }

  if (has_kanji || has_japanese_symbol) {
    return Util::KANJI;
  }
  if (has_katakana) {
    return Util::KATAKANA;
  }
  if (has_hiragana) {
    return Util::HIRAGANA;
  }
  return Util::SCRIPT_TYPE_SIZE;
}

// Extracts the last connective token from preceding text.
//
// For NUMBER/ALPHABET tails, this intentionally keeps the old behavior:
//   - "C60"  -> "60" / NUMBER
//   - "200x" -> "x"  / ALPHABET
//
// For Japanese tails, this extracts a mixed Japanese-looking chunk:
//   - "東京"       -> "東京" / KANJI
//   - "山田さん"   -> "山田さん" / KANJI
//   - "ファイル"   -> "ファイル" / KATAKANA
//   - "Issue番号" -> "Issue番号" / KANJI
//   - "〇〇"       -> "〇〇" / KANJI
//
// If text ends with a hard boundary such as a space or punctuation, returns
// false.
bool ExtractLastTokenWithScriptType(const absl::string_view text,
                                    std::string* last_token,
                                    Util::ScriptType* last_script_type) {
  last_token->clear();
  *last_script_type = Util::SCRIPT_TYPE_SIZE;

  ConstChar32ReverseIterator iter(text);
  if (iter.Done()) {
    return false;
  }

  if (IsHardBoundary(iter.Get())) {
    return false;
  }

  std::vector<char32_t> reverse_last_token;
  const char32_t tail_codepoint = iter.Get();
  const Util::ScriptType tail_script_type =
      Util::GetScriptType(tail_codepoint);

  if (IsJapaneseScriptType(tail_script_type) ||
      IsJapaneseTokenSymbol(tail_codepoint)) {
    for (; !iter.Done(); iter.Next()) {
      const char32_t codepoint = iter.Get();
      if (IsHardBoundary(codepoint) || !IsJapaneseChunkChar(codepoint)) {
        break;
      }
      reverse_last_token.push_back(codepoint);
    }

    if (reverse_last_token.empty()) {
      return false;
    }

    *last_script_type = ClassifyJapaneseChunk(reverse_last_token);
  } else {
    for (; !iter.Done(); iter.Next()) {
      const char32_t codepoint = iter.Get();
      if (IsHardBoundary(codepoint) ||
          Util::GetScriptType(codepoint) != tail_script_type) {
        break;
      }
      reverse_last_token.push_back(codepoint);
    }

    if (reverse_last_token.empty()) {
      return false;
    }

    *last_script_type = tail_script_type;
  }

  if (*last_script_type == Util::SCRIPT_TYPE_SIZE) {
    return false;
  }

  // TODO(yukawa): Replace reverse_iterator with const_reverse_iterator when
  //     build failure on Android is fixed.
  for (std::vector<char32_t>::reverse_iterator it = reverse_last_token.rbegin();
       it != reverse_last_token.rend(); ++it) {
    Util::CodepointToUtf8Append(*it, last_token);
  }
  return !last_token->empty();
}

}  // namespace

HistoryReconstructor::HistoryReconstructor(
    const dictionary::PosMatcher& pos_matcher)
    : pos_matcher_(pos_matcher) {}

bool HistoryReconstructor::ReconstructHistory(absl::string_view preceding_text,
                                              Segments* segments) const {
  std::string key;
  std::string value;
  uint16_t id;
  if (!GetLastConnectivePart(preceding_text, &key, &value, &id)) {
    return false;
  }

  Segment* segment = segments->add_segment();
  segment->set_key(key);
  segment->set_segment_type(Segment::HISTORY);
  Candidate* candidate = segment->push_back_candidate();
  candidate->rid = id;
  candidate->lid = id;
  candidate->content_key = key;
  candidate->key = std::move(key);
  candidate->content_value = value;
  candidate->value = std::move(value);
  candidate->attributes = Attribute::NO_LEARNING;
  return true;
}

bool HistoryReconstructor::GetLastConnectivePart(
    const absl::string_view preceding_text, std::string* key,
    std::string* value, uint16_t* id) const {
  key->clear();
  value->clear();
  *id = pos_matcher_.GetGeneralNounId();

  Util::ScriptType last_script_type = Util::SCRIPT_TYPE_SIZE;
  std::string last_token;
  if (!ExtractLastTokenWithScriptType(preceding_text, &last_token,
                                      &last_script_type)) {
    return false;
  }

  switch (last_script_type) {
    case Util::NUMBER: {
      *key = japanese_util::FullWidthAsciiToHalfWidthAscii(last_token);
      *value = std::move(last_token);
      *id = pos_matcher_.GetNumberId();
      return true;
    }
    case Util::ALPHABET: {
      *key = japanese_util::FullWidthAsciiToHalfWidthAscii(last_token);
      *value = std::move(last_token);
      *id = pos_matcher_.GetUniqueNounId();
      return true;
    }
    case Util::KANJI:
    case Util::KATAKANA: {
      *key = last_token;
      *value = std::move(last_token);
      *id = pos_matcher_.GetGeneralNounId();
      return true;
    }
    case Util::HIRAGANA:
      // Pure hiragana preceding text such as "あ", "に", "して" is too
      // ambiguous to be reconstructed as a noun-like history segment.
      return false;
    default:
      return false;
  }
}

}  // namespace converter
}  // namespace mozc
