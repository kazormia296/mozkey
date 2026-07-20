// Copyright 2026 The Mozkey Authors

#include "zenz_scorer/json_parser.h"

#include <string>
#include <string_view>

#include "testing/gunit.h"

namespace mozc::zenz_scorer {
namespace {

std::string JsonWithRawValue(std::string_view value) {
  return std::string("{\"content\":\"") + std::string(value) + "\"}";
}

TEST(JsonParserTest, DecodesBmpEscape) {
  std::string output;
  EXPECT_TRUE(
      ExtractJsonStringField(R"({"content":"\u3042"})", "content", &output));
  EXPECT_EQ(output, "あ");
}

TEST(JsonParserTest, CombinesSurrogatePair) {
  std::string output;
  EXPECT_TRUE(ExtractJsonStringField(
      R"({"content":"\uD83D\uDE00"})", "content", &output));
  EXPECT_EQ(output, "😀");
}

TEST(JsonParserTest, RejectsLoneAndMalformedSurrogates) {
  for (const char* json : {
           R"({"content":"\uD83D"})",
           R"({"content":"\uDE00"})",
           R"({"content":"\uD83D\u0041"})",
           R"({"content":"\uD83D\uD83D"})",
       }) {
    std::string output;
    EXPECT_FALSE(ExtractJsonStringField(json, "content", &output)) << json;
  }
}

TEST(JsonParserTest, AcceptsValidRawUtf8IncludingFourByteScalars) {
  std::string output;
  EXPECT_TRUE(
      ExtractJsonStringField(JsonWithRawValue("あ😀"), "content", &output));
  EXPECT_EQ(output, "あ😀");

  const std::string maximum_scalar("\xF4\x8F\xBF\xBF", 4);
  EXPECT_TRUE(ExtractJsonStringField(JsonWithRawValue(maximum_scalar),
                                     "content", &output));
  EXPECT_EQ(output, maximum_scalar);
}

TEST(JsonParserTest, RejectsOutOfRangeAndSurrogateUtf8) {
  for (const std::string& value : {
           std::string("\xF4\x90\x80\x80", 4),
           std::string("\xED\xA0\x80", 3),
       }) {
    std::string output;
    EXPECT_FALSE(ExtractJsonStringField(JsonWithRawValue(value), "content",
                                        &output));
  }
}

TEST(JsonParserTest, RejectsOverlongUtf8) {
  for (const std::string& value : {
           std::string("\xC0\xAF", 2),
           std::string("\xE0\x80\xAF", 3),
           std::string("\xF0\x80\x80\xAF", 4),
       }) {
    std::string output;
    EXPECT_FALSE(ExtractJsonStringField(JsonWithRawValue(value), "content",
                                        &output));
  }
}

TEST(JsonParserTest, RejectsInvalidContinuationBytes) {
  for (const std::string& value : {
           std::string("\x80", 1),
           std::string("\xE2\x28\xA1", 3),
           std::string("\xF0\x9F\x98", 3),
       }) {
    std::string output;
    EXPECT_FALSE(ExtractJsonStringField(JsonWithRawValue(value), "content",
                                        &output));
  }
}

TEST(JsonParserTest, RejectsUnescapedControlCharacters) {
  std::string output;
  EXPECT_FALSE(ExtractJsonStringField(JsonWithRawValue("line\nbreak"),
                                      "content", &output));
}

TEST(JsonParserTest, RequiresAnExactTopLevelStringField) {
  std::string output;
  EXPECT_FALSE(ExtractJsonStringField(
      R"({"role":"content","other":"attacker"})", "content", &output));
  EXPECT_FALSE(ExtractJsonStringField(
      R"({"outer":{"content":"nested"}})", "content", &output));
  EXPECT_FALSE(
      ExtractJsonStringField(R"({"content":123})", "content", &output));
  EXPECT_FALSE(ExtractJsonStringField(R"([{"content":"array"}])", "content",
                                      &output));
}

TEST(JsonParserTest, RejectsIncompleteOrTrailingDocuments) {
  std::string output;
  EXPECT_FALSE(
      ExtractJsonStringField(R"({"content":"accepted")", "content", &output));
  EXPECT_FALSE(ExtractJsonStringField(
      R"({"content":"accepted"} trailing)", "content", &output));
}

TEST(JsonParserTest, RejectsAmbiguousOrNonstandardObjectSyntax) {
  std::string output;
  EXPECT_FALSE(ExtractJsonStringField(
      R"({"content":"first","content":"second"})", "content", &output));
  EXPECT_FALSE(ExtractJsonStringField(R"({"content":"accepted",})",
                                      "content", &output));
  EXPECT_FALSE(ExtractJsonStringField(
      R"({"content":"accepted"/* comment */})", "content", &output));
}

TEST(JsonParserTest, DecodesEscapedFieldNames) {
  std::string output;
  EXPECT_TRUE(ExtractJsonStringField(
      R"({"con\u0074ent":"accepted"})", "content", &output));
  EXPECT_EQ(output, "accepted");
}

}  // namespace
}  // namespace mozc::zenz_scorer
