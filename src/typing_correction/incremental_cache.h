// Copyright 2026 Grimodex contributors.

#ifndef MOZC_TYPING_CORRECTION_INCREMENTAL_CACHE_H_
#define MOZC_TYPING_CORRECTION_INCREMENTAL_CACHE_H_

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "absl/strings/string_view.h"
#include "typing_correction/roman_typing_corrector.h"

namespace mozc::typing_correction {

// A bounded, session-local cache for raw Roman hypothesis enumeration.  It
// never stores user text outside the owning EngineConverter and has no
// telemetry or persistence path.
class IncrementalRomanCache final {
 public:
  explicit IncrementalRomanCache(size_t max_entries = 32)
      : max_entries_(max_entries) {}

  std::vector<Hypothesis> GetOrGenerate(absl::string_view raw,
                                        const Limits& limits,
                                        const RomanTypingCorrector& corrector);

  void Clear();
  size_t hit_count() const { return hit_count_; }
  size_t miss_count() const { return miss_count_; }

 private:
  std::optional<std::vector<Hypothesis>> FindPrefixReuse(
      absl::string_view raw, const Limits& limits) const;
  void Store(std::string raw, std::vector<Hypothesis> hypotheses);

  size_t max_entries_;
  std::map<std::string, std::vector<Hypothesis>> entries_;
  size_t hit_count_ = 0;
  size_t miss_count_ = 0;
};

struct LocalCorrectionDecision {
  std::string original_raw;
  std::string corrected_raw;
  int64_t timestamp_msec = 0;
  bool accepted = false;
};

// Timestamp-ordered local accept/reject state.  A stale callback cannot
// overwrite a newer decision, and the ledger is intentionally memory-only.
class LocalCorrectionDecisionLedger final {
 public:
  bool Record(absl::string_view original_raw, absl::string_view corrected_raw,
              int64_t timestamp_msec, bool accepted);

  std::optional<LocalCorrectionDecision> Find(
      absl::string_view original_raw) const;
  void Clear();

 private:
  std::map<std::string, LocalCorrectionDecision> decisions_;
  int64_t latest_timestamp_msec_ = 0;
};

}  // namespace mozc::typing_correction

#endif  // MOZC_TYPING_CORRECTION_INCREMENTAL_CACHE_H_
