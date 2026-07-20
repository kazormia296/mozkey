// Copyright 2026 The Mozkey Authors

#ifndef MOZC_ZENZ_SCORER_JSON_PARSER_H_
#define MOZC_ZENZ_SCORER_JSON_PARSER_H_

#include <string>

namespace mozc::zenz_scorer {

// Extracts and decodes a JSON string field.  Returns false when the field is
// absent, is not a string, or contains invalid JSON escaping or UTF-8.
bool ExtractJsonStringField(const std::string& json, const std::string& field,
                            std::string* output);

}  // namespace mozc::zenz_scorer

#endif  // MOZC_ZENZ_SCORER_JSON_PARSER_H_
