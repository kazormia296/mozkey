// Copyright 2026 The Mozkey Authors

#include "zenz_scorer/json_parser.h"

#include <string>

#include "google/protobuf/struct.pb.h"
#include "google/protobuf/util/json_util.h"

namespace mozc::zenz_scorer {
namespace {

bool HasStrictJsonLexicalForm(const std::string& json) {
  bool in_string = false;
  bool escaped = false;
  unsigned char last_significant = 0;
  for (const unsigned char byte : json) {
    if (in_string) {
      // JSON strings cannot contain any raw C0 control character.  Protobuf's
      // JSON conversion intentionally accepts a few legacy forms, so enforce
      // this part of the wire grammar before asking it to decode the object.
      if (byte < 0x20) {
        return false;
      }
      if (escaped) {
        escaped = false;
      } else if (byte == '\\') {
        escaped = true;
      } else if (byte == '"') {
        in_string = false;
      }
      continue;
    }

    if (byte == '"') {
      in_string = true;
      last_significant = byte;
    } else if (byte < 0x20 && byte != '\t' && byte != '\n' && byte != '\r') {
      return false;
    } else if (byte != ' ' && byte != '\t' && byte != '\n' && byte != '\r') {
      if ((byte == '}' || byte == ']') && last_significant == ',') {
        return false;
      }
      last_significant = byte;
    }
  }
  return !in_string && !escaped;
}

}  // namespace

bool ExtractJsonStringField(const std::string& json, const std::string& field,
                            std::string* output) {
  if (output == nullptr) {
    return false;
  }
  output->clear();

  if (!HasStrictJsonLexicalForm(json)) {
    return false;
  }

  google::protobuf::Struct object;
  if (!google::protobuf::util::JsonStringToMessage(json, &object).ok()) {
    return false;
  }

  const auto entry = object.fields().find(field);
  if (entry == object.fields().end() ||
      entry->second.kind_case() != google::protobuf::Value::kStringValue) {
    return false;
  }

  *output = entry->second.string_value();
  return true;
}

}  // namespace mozc::zenz_scorer
