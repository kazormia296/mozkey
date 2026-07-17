// Copyright 2026 Grimodex Contributors
//
// Licensed under the same terms as Mozc.

#include "converter/node.h"

#include "dictionary/dictionary_token.h"
#include "testing/gunit.h"

namespace mozc {
namespace {

TEST(NodeTest, PropagatesProjectDictionaryProvenance) {
  const dictionary::Token token("key", "value", 3000, 10, 10,
                                dictionary::Token::PROJECT_DICTIONARY);
  Node node;
  node.InitFromToken(token);

  EXPECT_NE(node.attributes & Node::PROJECT_DICTIONARY, 0);
  EXPECT_NE(node.attributes & Node::NO_VARIANTS_EXPANSION, 0);
  EXPECT_EQ(node.attributes & Node::USER_DICTIONARY, 0);
}

}  // namespace
}  // namespace mozc
