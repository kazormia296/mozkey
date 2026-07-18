// Copyright 2026 The Mozkey Authors

#include "win32/tip/tip_edit_session.h"

#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>

#include "protocol/commands.pb.h"
#include "testing/gunit.h"

namespace mozc::win32::tsf {
namespace {

TEST(TipEditSessionTest, AcceptsMatchingNegativeDeletionRange) {
  commands::DeletionRange deletion_range;
  deletion_range.set_offset(-3);
  deletion_range.set_length(3);

  const std::optional<std::size_t> length =
      GetValidPrecedingDeletionLength(deletion_range);
  ASSERT_TRUE(length.has_value());
  EXPECT_EQ(*length, 3);
}

TEST(TipEditSessionTest, RejectsUnsupportedDeletionRanges) {
  commands::DeletionRange deletion_range;

  deletion_range.set_offset(1);
  deletion_range.set_length(1);
  EXPECT_FALSE(GetValidPrecedingDeletionLength(deletion_range).has_value());

  deletion_range.set_offset(0);
  deletion_range.set_length(0);
  EXPECT_FALSE(GetValidPrecedingDeletionLength(deletion_range).has_value());

  deletion_range.set_offset(-3);
  deletion_range.set_length(2);
  EXPECT_FALSE(GetValidPrecedingDeletionLength(deletion_range).has_value());

  deletion_range.set_offset(-3);
  deletion_range.set_length(-3);
  EXPECT_FALSE(GetValidPrecedingDeletionLength(deletion_range).has_value());
}

TEST(TipEditSessionTest, RejectsInt32MinWithoutSignedOverflow) {
  commands::DeletionRange deletion_range;
  deletion_range.set_offset(std::numeric_limits<int32_t>::min());
  deletion_range.set_length(std::numeric_limits<int32_t>::max());

  EXPECT_FALSE(GetValidPrecedingDeletionLength(deletion_range).has_value());
}

TEST(TipEditSessionTest, BoundsDeletionBeforeSyntheticBackspaceFallback) {
  commands::DeletionRange deletion_range;
  deletion_range.set_offset(-static_cast<int32_t>(kMaxTsfDeletionLength));
  deletion_range.set_length(static_cast<int32_t>(kMaxTsfDeletionLength));
  EXPECT_EQ(GetValidPrecedingDeletionLength(deletion_range),
            kMaxTsfDeletionLength);

  deletion_range.set_offset(
      -static_cast<int32_t>(kMaxTsfDeletionLength + 1));
  deletion_range.set_length(
      static_cast<int32_t>(kMaxTsfDeletionLength + 1));
  EXPECT_FALSE(GetValidPrecedingDeletionLength(deletion_range).has_value());
}

}  // namespace
}  // namespace mozc::win32::tsf
