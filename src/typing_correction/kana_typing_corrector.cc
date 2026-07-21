// Copyright 2026 Grimodex contributors.

#include "typing_correction/kana_typing_corrector.h"

#include <algorithm>
#include <cstddef>
#include <queue>
#include <string>
#include <utility>

#include "base/util.h"
#include "typing_correction/generated_kana_rules.h"

namespace mozc::typing_correction {
namespace {

bool SameEvent(const commands::KeyEvent& lhs, const commands::KeyEvent& rhs) {
  return lhs.SerializeAsString() == rhs.SerializeAsString();
}

std::string RawFromEvents(absl::Span<const commands::KeyEvent> events) {
  std::string raw;
  for (const commands::KeyEvent& event : events) {
    if (event.has_key_code()) {
      raw.append(Util::CodepointToUtf8(event.key_code()));
    } else if (event.has_key_string()) {
      raw.append(event.key_string());
    }
  }
  return raw;
}

std::string EventSignature(const commands::KeyEvent& event) {
  return event.SerializeAsString();
}

bool Matches(const KanaRule& rule, const commands::KeyEvent& event) {
  return (rule.wrong_key_code < 0 ||
          (event.has_key_code() && event.key_code() == rule.wrong_key_code)) &&
         (rule.wrong_key_string.empty() ||
          (event.has_key_string() &&
           event.key_string() == rule.wrong_key_string));
}

Hypothesis MakeRuleHypothesis(absl::Span<const commands::KeyEvent> events,
                              size_t index, const KanaRule& rule) {
  Hypothesis result;
  result.original_raw = RawFromEvents(events);
  result.corrected_key_events.assign(events.begin(), events.end());
  commands::KeyEvent& replacement = result.corrected_key_events[index];
  if (rule.corrected_key_code >= 0) {
    replacement.set_key_code(rule.corrected_key_code);
  }
  if (!rule.corrected_key_string.empty()) {
    replacement.set_key_string(std::string(rule.corrected_key_string));
  } else {
    replacement.clear_key_string();
  }
  result.corrected_raw = RawFromEvents(result.corrected_key_events);
  result.edits.push_back(Edit{index,
                              1,
                              result.corrected_raw,
                              rule.operation,
                              rule.cost,
                              std::string(rule.rule_id)});
  result.edit_cost = rule.cost;
  result.auto_applicable = rule.auto_applicable;
  return result;
}

bool IsBetter(const Hypothesis& lhs, const Hypothesis& rhs) {
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

void AddCandidate(const absl::Span<const commands::KeyEvent> original_events,
                  const Limits& limits, Hypothesis candidate,
                  std::vector<Hypothesis>* candidates) {
  if (candidate.edits.empty() || candidate.edits.size() > limits.max_edits ||
      candidate.edit_cost > limits.max_edit_cost ||
      candidate.corrected_key_events.empty()) {
    return;
  }

  bool changed = original_events.size() != candidate.corrected_key_events.size();
  if (!changed) {
    for (size_t i = 0; i < original_events.size(); ++i) {
      if (!SameEvent(original_events[i], candidate.corrected_key_events[i])) {
        changed = true;
        break;
      }
    }
  }
  if (!changed) {
    return;
  }

  const std::string signature = [&candidate] {
    std::string value;
    for (const commands::KeyEvent& event : candidate.corrected_key_events) {
      value.append(EventSignature(event));
      value.push_back('\0');
    }
    return value;
  }();
  for (Hypothesis& existing : *candidates) {
    std::string existing_signature;
    for (const commands::KeyEvent& event : existing.corrected_key_events) {
      existing_signature.append(EventSignature(event));
      existing_signature.push_back('\0');
    }
    if (existing_signature == signature) {
      if (IsBetter(candidate, existing)) {
        existing = std::move(candidate);
      }
      return;
    }
  }
  candidates->push_back(std::move(candidate));
}

struct WorseHypothesisFirst {
  bool operator()(const Hypothesis& lhs, const Hypothesis& rhs) const {
    return IsBetter(lhs, rhs);
  }
};

std::string JisRows() {
  return "1234567890-^qwertyuiop@[asdfghjkl;:]zxcvbnm,./";
}

}  // namespace

absl::Span<const KanaRule> DefaultKanaRules() {
  return absl::Span<const KanaRule>(kGeneratedKanaRules,
                                    kGeneratedKanaRuleCount);
}

absl::Span<const KanaGoldCase> KanaGoldCases() {
  return absl::Span<const KanaGoldCase>(kGeneratedKanaGoldCases,
                                        kGeneratedKanaGoldCaseCount);
}

absl::Span<const KanaNegativeCase> KanaNegativeCases() {
  return absl::Span<const KanaNegativeCase>(kGeneratedKanaNegativeCases,
                                            kGeneratedKanaNegativeCaseCount);
}

absl::string_view JisKanaKeyString(const int32_t key_code) {
  switch (key_code) {
    case '1':
      return "ぬ";
    case '2':
      return "ふ";
    case '3':
      return "あ";
    case '4':
      return "う";
    case '5':
      return "え";
    case '6':
      return "お";
    case '7':
      return "や";
    case '8':
      return "ゆ";
    case '9':
      return "よ";
    case '0':
      return "わ";
    case '-':
      return "ほ";
    case '^':
      return "へ";
    case 'q':
      return "た";
    case 'w':
      return "て";
    case 'e':
      return "い";
    case 'r':
      return "す";
    case 't':
      return "か";
    case 'y':
      return "ん";
    case 'u':
      return "な";
    case 'i':
      return "に";
    case 'o':
      return "ら";
    case 'p':
      return "せ";
    case '@':
      return "゛";
    case '[':
      return "゜";
    case 'a':
      return "ち";
    case 's':
      return "と";
    case 'd':
      return "し";
    case 'f':
      return "は";
    case 'g':
      return "き";
    case 'h':
      return "く";
    case 'j':
      return "ま";
    case 'k':
      return "の";
    case 'l':
      return "り";
    case ';':
      return "れ";
    case ':':
      return "け";
    case ']':
      return "む";
    case 'z':
      return "つ";
    case 'x':
      return "さ";
    case 'c':
      return "そ";
    case 'v':
      return "ひ";
    case 'b':
      return "こ";
    case 'n':
      return "み";
    case 'm':
      return "も";
    case ',':
      return "ね";
    case '.':
      return "る";
    case '/':
      return "め";
    case '_':
      return "ろ";
    default:
      return {};
  }
}

absl::string_view JisKanaPhysicalNeighbors(const int32_t key_code) {
  static thread_local std::string neighbors;
  neighbors.clear();
  const char key = static_cast<char>(key_code);
  const std::string rows = JisRows();
  const size_t position = rows.find(key);
  if (position == std::string::npos) {
    return {};
  }
  // The row widths are fixed by the JIS layout.  Horizontal neighbors are
  // always safe; the coarse vertical neighbors are candidate-only.
  size_t row_start = 0;
  size_t row_end = 0;
  for (const size_t end : {size_t(12), size_t(24), size_t(36), rows.size()}) {
    if (position < end) {
      row_end = end;
      break;
    }
    row_start = end;
  }
  if (position > row_start) {
    neighbors.push_back(rows[position - 1]);
  }
  if (position + 1 < row_end) {
    neighbors.push_back(rows[position + 1]);
  }
  const size_t row_offset = position - row_start;
  const size_t next_start = row_end;
  if (next_start < rows.size()) {
    const size_t next_width =
        next_start == 12 ? 12 : (next_start == 24 ? 12 : rows.size() - next_start);
    if (row_offset < next_width) {
      neighbors.push_back(rows[next_start + row_offset]);
    }
  }
  if (row_start > 0) {
    const size_t previous_start = row_start == 12 ? 0 : (row_start == 24 ? 12 : 24);
    const size_t previous_width = row_start == 12 ? 12 : 12;
    if (row_offset < previous_width) {
      neighbors.push_back(rows[previous_start + row_offset]);
    }
  }
  return neighbors;
}

std::vector<Hypothesis> KanaTypingCorrector::Generate(
    const absl::Span<const commands::KeyEvent> events) const {
  return Generate(events, Limits());
}

std::vector<Hypothesis> KanaTypingCorrector::Generate(
    const absl::Span<const commands::KeyEvent> events,
    const Limits& limits) const {
  std::vector<Hypothesis> candidates;
  if (events.empty() || limits.max_edits != 1 || limits.max_edits == 0 ||
      limits.max_raw_hypotheses == 0 || limits.max_edit_cost < 0) {
    return candidates;
  }

  for (size_t index = 0; index < events.size(); ++index) {
    for (const KanaRule& rule : rules_) {
      if (Matches(rule, events[index])) {
        AddCandidate(events, limits,
                     MakeRuleHypothesis(events, index, rule), &candidates);
      }
    }

    const commands::KeyEvent& event = events[index];
    if (event.has_key_code() && event.has_key_string() &&
        event.modifier_keys_size() == 0 &&
        JisKanaKeyString(event.key_code()) == event.key_string()) {
      for (const char neighbor : JisKanaPhysicalNeighbors(event.key_code())) {
        const absl::string_view target_string = JisKanaKeyString(neighbor);
        if (target_string.empty()) {
          continue;
        }
        KanaRule generic;
        generic.wrong_key_code = event.key_code();
        generic.wrong_key_string = event.key_string();
        generic.corrected_key_code = neighbor;
        generic.corrected_key_string = target_string;
        generic.operation = Operation::kNeighborSubstitution;
        generic.cost = 180;
        generic.auto_applicable = false;
        generic.rule_id = "GEN-JIS-NEIGHBOR";
        AddCandidate(events, limits,
                     MakeRuleHypothesis(events, index, generic), &candidates);
      }
    }
  }

  for (size_t index = 1; index < events.size(); ++index) {
    if (SameEvent(events[index - 1], events[index])) {
      Hypothesis candidate;
      candidate.original_raw = RawFromEvents(events);
      candidate.corrected_key_events.assign(events.begin(), events.end());
      candidate.corrected_key_events.erase(
          candidate.corrected_key_events.begin() + index);
      candidate.corrected_raw = RawFromEvents(candidate.corrected_key_events);
      candidate.edits.push_back(Edit{index,
                                     1,
                                     "",
                                     Operation::kDuplicateRemoval,
                                     170,
                                     "GEN-JIS-DUPLICATE"});
      candidate.edit_cost = 170;
      AddCandidate(events, limits, std::move(candidate), &candidates);
    }
  }

  std::priority_queue<Hypothesis, std::vector<Hypothesis>,
                      WorseHypothesisFirst>
      best_candidates;
  for (Hypothesis& candidate : candidates) {
    best_candidates.push(std::move(candidate));
    if (best_candidates.size() > limits.max_raw_hypotheses) {
      best_candidates.pop();
    }
  }
  candidates.clear();
  while (!best_candidates.empty()) {
    candidates.push_back(best_candidates.top());
    best_candidates.pop();
  }
  std::sort(candidates.begin(), candidates.end(),
            [](const Hypothesis& lhs, const Hypothesis& rhs) {
              return IsBetter(lhs, rhs);
            });
  return candidates;
}

}  // namespace mozc::typing_correction
