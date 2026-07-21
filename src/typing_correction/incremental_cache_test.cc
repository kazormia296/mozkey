// Copyright 2026 Grimodex contributors.

#include "typing_correction/incremental_cache.h"

#include <algorithm>
#include <string>

#include "gtest/gtest.h"

namespace mozc::typing_correction {
namespace {

TEST(IncrementalRomanCacheTest, ReusesPrefixAndRetainsNewlyEligibleRules) {
  IncrementalRomanCache cache;
  RomanTypingCorrector corrector;
  Limits limits;

  const std::vector<Hypothesis> first =
      cache.GetOrGenerate("onegia", limits, corrector);
  ASSERT_FALSE(first.empty());
  const size_t misses = cache.miss_count();

  const std::vector<Hypothesis> extended =
      cache.GetOrGenerate("onegiam", limits, corrector);
  EXPECT_GT(cache.hit_count(), 0);
  EXPECT_EQ(cache.miss_count(), misses);
  bool saw_prefix_reused_hypothesis = false;
  for (const Hypothesis& hypothesis : extended) {
    EXPECT_EQ(hypothesis.original_raw, "onegiam");
    saw_prefix_reused_hypothesis |= hypothesis.corrected_raw.ends_with('m');
  }
  EXPECT_TRUE(saw_prefix_reused_hypothesis);

  cache.Clear();
  cache.GetOrGenerate("kudasi", limits, corrector);
  const std::vector<Hypothesis> completed =
      cache.GetOrGenerate("kudasia", limits, corrector);
  const auto corpus_rule = std::find_if(
      completed.begin(), completed.end(), [](const Hypothesis& hypothesis) {
        return hypothesis.corrected_raw == "kudasai" &&
               hypothesis.auto_applicable;
      });
  EXPECT_NE(corpus_rule, completed.end());
}

TEST(IncrementalRomanCacheTest, StaleLocalDecisionCannotWinTimestampRace) {
  LocalCorrectionDecisionLedger ledger;
  EXPECT_TRUE(ledger.Record("kudasia", "kudasai", 10, true));
  EXPECT_FALSE(ledger.Record("kudasia", "kudaisa", 9, false));
  ASSERT_TRUE(ledger.Find("kudasia").has_value());
  EXPECT_TRUE(ledger.Find("kudasia")->accepted);
  EXPECT_FALSE(ledger.Record("kudasia", "kudaisa", 10, false));
  EXPECT_TRUE(ledger.Record("kudasia", "kudaisa", 11, false));
  EXPECT_FALSE(ledger.Find("kudasia")->accepted);
}

}  // namespace
}  // namespace mozc::typing_correction
