// Copyright 2026 Grimodex contributors.

#include "typing_correction/incremental_cache.h"

#include <algorithm>
#include <cstddef>
#include <utility>

namespace mozc::typing_correction {
namespace {

bool IsBetterCachedHypothesis(const Hypothesis& lhs,
                              const Hypothesis& rhs) {
  if (lhs.auto_applicable != rhs.auto_applicable) {
    return lhs.auto_applicable;
  }
  const bool lhs_is_generic =
      !lhs.edits.empty() && lhs.edits.front().rule_id.starts_with("GEN-");
  const bool rhs_is_generic =
      !rhs.edits.empty() && rhs.edits.front().rule_id.starts_with("GEN-");
  if (lhs_is_generic != rhs_is_generic) {
    return !lhs_is_generic;
  }
  if (lhs.edit_cost != rhs.edit_cost) {
    return lhs.edit_cost < rhs.edit_cost;
  }
  return lhs.corrected_raw < rhs.corrected_raw;
}

void MergeHypotheses(std::vector<Hypothesis> fresh,
                     std::vector<Hypothesis> reused, const Limits& limits,
                     std::vector<Hypothesis>* result) {
  std::map<std::string, Hypothesis> by_corrected_raw;
  for (Hypothesis& hypothesis : fresh) {
    const std::string corrected_raw = hypothesis.corrected_raw;
    by_corrected_raw[corrected_raw] = std::move(hypothesis);
  }
  for (Hypothesis& hypothesis : reused) {
    const std::string corrected_raw = hypothesis.corrected_raw;
    const auto iterator = by_corrected_raw.find(corrected_raw);
    if (iterator == by_corrected_raw.end()) {
      by_corrected_raw.emplace(corrected_raw, std::move(hypothesis));
    } else if (IsBetterCachedHypothesis(hypothesis, iterator->second)) {
      iterator->second = std::move(hypothesis);
    }
  }

  result->clear();
  result->reserve(by_corrected_raw.size());
  for (auto& [unused_raw, hypothesis] : by_corrected_raw) {
    result->push_back(std::move(hypothesis));
  }
  std::sort(result->begin(), result->end(),
            [](const Hypothesis& lhs, const Hypothesis& rhs) {
              return IsBetterCachedHypothesis(lhs, rhs);
            });
  if (result->size() > limits.max_raw_hypotheses) {
    result->resize(limits.max_raw_hypotheses);
  }
}

}  // namespace

std::optional<std::vector<Hypothesis>> IncrementalRomanCache::FindPrefixReuse(
    const absl::string_view raw, const Limits& limits) const {
  const std::map<std::string, std::vector<Hypothesis>>::const_iterator exact =
      entries_.find(std::string(raw));
  if (exact != entries_.end()) {
    return exact->second;
  }

  const std::map<std::string, std::vector<Hypothesis>>::const_iterator longest =
      std::max_element(
          entries_.begin(), entries_.end(),
          [raw](const auto& lhs, const auto& rhs) {
            const bool lhs_prefix = raw.starts_with(lhs.first);
            const bool rhs_prefix = raw.starts_with(rhs.first);
            if (lhs_prefix != rhs_prefix) {
              return !lhs_prefix;
            }
            return lhs.first.size() < rhs.first.size();
          });
  if (longest == entries_.end() || !raw.starts_with(longest->first) ||
      longest->first.size() == raw.size()) {
    return std::nullopt;
  }

  const absl::string_view suffix = raw.substr(longest->first.size());
  std::vector<Hypothesis> reused;
  for (Hypothesis hypothesis : longest->second) {
    if (hypothesis.edits.empty() || hypothesis.corrected_raw.empty()) {
      continue;
    }
    const Edit& edit = hypothesis.edits.front();
    if (edit.raw_start + edit.raw_length > longest->first.size()) {
      continue;
    }
    hypothesis.original_raw = std::string(raw);
    hypothesis.corrected_raw.append(suffix.data(), suffix.size());
    reused.push_back(std::move(hypothesis));
  }
  if (reused.empty()) {
    return std::nullopt;
  }
  std::sort(reused.begin(), reused.end(),
            [](const Hypothesis& lhs, const Hypothesis& rhs) {
              if (lhs.auto_applicable != rhs.auto_applicable) {
                return lhs.auto_applicable;
              }
              if (lhs.edit_cost != rhs.edit_cost) {
                return lhs.edit_cost < rhs.edit_cost;
              }
              return lhs.corrected_raw < rhs.corrected_raw;
            });
  if (reused.size() > limits.max_raw_hypotheses) {
    reused.resize(limits.max_raw_hypotheses);
  }
  return reused;
}

void IncrementalRomanCache::Store(std::string raw,
                                  std::vector<Hypothesis> hypotheses) {
  if (max_entries_ == 0) {
    return;
  }
  entries_[std::move(raw)] = std::move(hypotheses);
  while (entries_.size() > max_entries_) {
    entries_.erase(entries_.begin());
  }
}

std::vector<Hypothesis> IncrementalRomanCache::GetOrGenerate(
    const absl::string_view raw, const Limits& limits,
    const RomanTypingCorrector& corrector) {
  const auto exact = entries_.find(std::string(raw));
  if (exact != entries_.end()) {
    ++hit_count_;
    return exact->second;
  }

  if (const std::optional<std::vector<Hypothesis>> reused =
          FindPrefixReuse(raw, limits);
      reused.has_value()) {
    ++hit_count_;
    // Prefix replay is a fast source of safe existing hypotheses, but it can
    // miss a corpus rule whose wrong spelling ends at the newly typed suffix
    // (for example, "kudasia" -> "kudasai").  Re-enumerate the current raw
    // input and merge both sets so the cache never hides newly eligible rules.
    std::vector<Hypothesis> merged;
    MergeHypotheses(corrector.Generate(raw, limits), *reused, limits, &merged);
    Store(std::string(raw), merged);
    return merged;
  }

  ++miss_count_;
  std::vector<Hypothesis> generated = corrector.Generate(raw, limits);
  Store(std::string(raw), generated);
  return generated;
}

void IncrementalRomanCache::Clear() {
  entries_.clear();
  hit_count_ = 0;
  miss_count_ = 0;
}

bool LocalCorrectionDecisionLedger::Record(
    const absl::string_view original_raw, const absl::string_view corrected_raw,
    const int64_t timestamp_msec, const bool accepted) {
  if (original_raw.empty() || corrected_raw.empty() || timestamp_msec <= 0 ||
      timestamp_msec <= latest_timestamp_msec_) {
    return false;
  }
  latest_timestamp_msec_ = timestamp_msec;
  decisions_[std::string(original_raw)] = LocalCorrectionDecision{
      std::string(original_raw), std::string(corrected_raw), timestamp_msec,
      accepted};
  return true;
}

std::optional<LocalCorrectionDecision> LocalCorrectionDecisionLedger::Find(
    const absl::string_view original_raw) const {
  const auto iterator = decisions_.find(std::string(original_raw));
  if (iterator == decisions_.end()) {
    return std::nullopt;
  }
  return iterator->second;
}

void LocalCorrectionDecisionLedger::Clear() {
  decisions_.clear();
  latest_timestamp_msec_ = 0;
}

}  // namespace mozc::typing_correction
