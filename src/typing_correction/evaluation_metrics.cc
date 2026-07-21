// Copyright 2026 Grimodex contributors.

#include <cstddef>
#include <functional>
#include <iomanip>
#include <iostream>
#include <map>
#include <ostream>
#include <string>
#include <utility>
#include <vector>

#include "base/util.h"
#include "composer/table.h"
#include "protocol/commands.pb.h"
#include "protocol/config.pb.h"
#include "typing_correction/composer_replayer.h"
#include "typing_correction/generated_holdout_cases.h"
#include "typing_correction/generated_kana_rules.h"
#include "typing_correction/generated_roman_rules.h"
#include "typing_correction/kana_typing_corrector.h"
#include "typing_correction/roman_typing_corrector.h"

#if defined(MOZKEY_ENGINE_METRICS)
#include "converter/converter_mock.h"
#include "converter/segments.h"
#include "engine/engine_converter.h"
#include "testing/gmock.h"
#endif

namespace mozc::typing_correction {
namespace {

struct Summary {
  size_t cases = 0;
  size_t rows_with_candidates = 0;
  size_t total_candidates = 0;
  size_t correct_candidates = 0;
  size_t correct_rows = 0;
  size_t top1_correct = 0;
  size_t auto_rows = 0;
  size_t auto_candidates = 0;
  size_t correct_auto_candidates = 0;
  size_t correct_auto_rows = 0;
  size_t raw_policy_forbidden_cases = 0;
  size_t raw_policy_forbidden_violations = 0;
  size_t raw_policy_forbidden_candidates = 0;
  size_t display_policy_forbidden_cases = 0;
  size_t display_policy_forbidden_violations = 0;
  size_t auto_policy_violations = 0;
  size_t suggest_cases = 0;
  size_t suggest_auto_violation_rows = 0;
  std::map<size_t, size_t> correct_rank;
};

double Rate(const size_t numerator, const size_t denominator) {
  return denominator == 0
             ? 0.0
             : static_cast<double>(numerator) / static_cast<double>(denominator);
}

std::string StratumValue(const absl::string_view stratum,
                         const absl::string_view key) {
  const std::string text(stratum);
  const std::string prefix = std::string(key) + "=";
  size_t start = 0;
  while (start <= text.size()) {
    const size_t end = text.find(';', start);
    const std::string field = text.substr(start, end - start);
    if (field.starts_with(prefix)) {
      return field.substr(prefix.size());
    }
    if (end == std::string::npos) {
      break;
    }
    start = end + 1;
  }
  return "unknown";
}

const char* MetricOperationName(const Operation operation) {
  switch (operation) {
    case Operation::kAdjacentTranspose:
      return "transpose";
    case Operation::kMissingKeyInsertion:
      return "omission";
    case Operation::kDuplicateRemoval:
      return "duplicate";
    case Operation::kLiteralReplacement:
      return "replacement";
    case Operation::kNeighborSubstitution:
      return "neighbor";
    case Operation::kKanaModifier:
      return "kana_modifier";
    case Operation::kInputModeReplay:
      return "input_mode_replay";
  }
  return "unknown";
}

void RecordNegative(Summary* summary,
                    const std::vector<Hypothesis>& hypotheses,
                    const bool raw_policy_forbidden,
                    const bool display_policy_forbidden,
                    const bool auto_forbidden) {
  ++summary->cases;
  summary->total_candidates += hypotheses.size();
  if (!hypotheses.empty()) {
    ++summary->rows_with_candidates;
  }
  if (raw_policy_forbidden) {
    ++summary->raw_policy_forbidden_cases;
    summary->raw_policy_forbidden_candidates += hypotheses.size();
    if (!hypotheses.empty()) {
      ++summary->raw_policy_forbidden_violations;
    }
  }
  if (display_policy_forbidden) {
    ++summary->display_policy_forbidden_cases;
    if (!hypotheses.empty()) {
      ++summary->display_policy_forbidden_violations;
    }
  }
  bool row_auto = false;
  for (const Hypothesis& hypothesis : hypotheses) {
    if (hypothesis.auto_applicable) {
      ++summary->auto_candidates;
      row_auto = true;
    }
  }
  if (row_auto) {
    ++summary->auto_rows;
    if (auto_forbidden) {
      ++summary->auto_policy_violations;
    }
  }
}

std::vector<commands::KeyEvent> MakeEvents(absl::string_view key_codes,
                                           absl::string_view key_strings) {
  std::vector<commands::KeyEvent> events;
  for (size_t index = 0; index < key_codes.size(); ++index) {
    commands::KeyEvent event;
    event.set_key_code(static_cast<int32_t>(key_codes[index]));
    event.set_key_string(
        std::string(Util::Utf8SubString(key_strings, index, 1)));
    events.push_back(std::move(event));
  }
  return events;
}

std::vector<Hypothesis> GenerateRomanEvaluationHypotheses(
    const RomanTypingCorrector& corrector, const absl::string_view raw) {
  RomanInputGateContext context;
  context.feature_enabled = true;
  context.is_roman_input = true;
  context.url_like = raw.find("://") != absl::string_view::npos;
  context.email_like = raw.find('@') != absl::string_view::npos;
  context.path_like = raw.find('/') != absl::string_view::npos ||
                      raw.find('\\') != absl::string_view::npos;
  if (!IsEligibleForRomanCorrection(context, raw)) {
    return {};
  }
  return corrector.Generate(raw);
}

std::vector<Hypothesis> GenerateKanaEvaluationHypotheses(
    const KanaTypingCorrector& corrector,
    const std::vector<commands::KeyEvent>& events,
    const absl::string_view raw) {
  KanaInputGateContext context;
  context.feature_enabled = true;
  context.is_jis_kana_input = true;
  if (!IsEligibleForKanaCorrection(context, events, raw)) {
    return {};
  }
  return corrector.Generate(events);
}

bool SameTrace(const std::vector<commands::KeyEvent>& events,
               absl::string_view key_codes, absl::string_view key_strings) {
  if (events.size() != key_codes.size()) {
    return false;
  }
  for (size_t index = 0; index < events.size(); ++index) {
    if (!events[index].has_key_code() ||
        events[index].key_code() != static_cast<int32_t>(key_codes[index]) ||
        !events[index].has_key_string() ||
        events[index].key_string() !=
            Util::Utf8SubString(key_strings, index, 1)) {
      return false;
    }
  }
  return true;
}

void RecordGoldSummary(Summary* summary,
                       const std::vector<Hypothesis>& hypotheses,
                       const std::function<bool(const Hypothesis&)>& matches,
                       const bool expected_auto) {
  ++summary->cases;
  summary->total_candidates += hypotheses.size();
  bool row_correct = false;
  bool row_correct_auto = false;
  bool row_auto = false;
  for (size_t index = 0; index < hypotheses.size(); ++index) {
    const Hypothesis& hypothesis = hypotheses[index];
    if (hypothesis.auto_applicable) {
      ++summary->auto_candidates;
      row_auto = true;
    }
    if (!matches(hypothesis)) {
      continue;
    }
    ++summary->correct_candidates;
    row_correct = true;
    ++summary->correct_rank[index + 1];
    if (hypothesis.auto_applicable) {
      ++summary->correct_auto_candidates;
      row_correct_auto = true;
    }
  }
  if (!hypotheses.empty()) {
    ++summary->rows_with_candidates;
    if (matches(hypotheses.front())) {
      ++summary->top1_correct;
    }
  }
  if (row_correct) {
    ++summary->correct_rows;
  }
  if (row_correct_auto) {
    ++summary->correct_auto_rows;
  }
  if (row_auto) {
    ++summary->auto_rows;
  }
  if (!expected_auto) {
    ++summary->suggest_cases;
    if (row_auto) {
      ++summary->suggest_auto_violation_rows;
    }
  }
}

void RecordRankedGold(
    Summary* total, std::map<std::string, Summary>* by_operation,
    std::map<std::string, Summary>* by_length,
    std::map<std::string, Summary>* by_position,
    std::map<std::string, Summary>* by_lexical,
    std::map<std::string, Summary>* by_feature, const std::string& operation,
    const std::string& length, const std::string& position,
    const std::string& lexical, const std::string& feature,
    const std::vector<Hypothesis>& hypotheses,
    const std::function<bool(const Hypothesis&)>& matches,
    const bool expected_auto) {
  RecordGoldSummary(total, hypotheses, matches, expected_auto);
  RecordGoldSummary(&(*by_operation)[operation], hypotheses, matches,
                    expected_auto);
  RecordGoldSummary(&(*by_length)[length], hypotheses, matches, expected_auto);
  RecordGoldSummary(&(*by_position)[position], hypotheses, matches,
                    expected_auto);
  RecordGoldSummary(&(*by_lexical)[lexical], hypotheses, matches,
                    expected_auto);
  RecordGoldSummary(&(*by_feature)[feature], hypotheses, matches,
                    expected_auto);
}

void RecordRankedNegative(
    Summary* total, std::map<std::string, Summary>* by_length,
    std::map<std::string, Summary>* by_position,
    std::map<std::string, Summary>* by_lexical,
    std::map<std::string, Summary>* by_feature, const std::string& length,
    const std::string& position, const std::string& lexical,
    const std::string& feature, const std::vector<Hypothesis>& hypotheses,
    const bool raw_policy_forbidden, const bool display_policy_forbidden,
    const bool auto_forbidden) {
  RecordNegative(total, hypotheses, raw_policy_forbidden,
                 display_policy_forbidden, auto_forbidden);
  RecordNegative(&(*by_length)[length], hypotheses, raw_policy_forbidden,
                 display_policy_forbidden, auto_forbidden);
  RecordNegative(&(*by_position)[position], hypotheses, raw_policy_forbidden,
                 display_policy_forbidden, auto_forbidden);
  RecordNegative(&(*by_lexical)[lexical], hypotheses, raw_policy_forbidden,
                 display_policy_forbidden, auto_forbidden);
  RecordNegative(&(*by_feature)[feature], hypotheses, raw_policy_forbidden,
                 display_policy_forbidden, auto_forbidden);
}

void PrintNumber(std::ostream& output, const char* name, size_t value,
                bool* first) {
  if (!*first) {
    output << ",";
  }
  output << "\n    \"" << name << "\": " << value;
  *first = false;
}

void PrintRate(std::ostream& output, const char* name, double value,
               bool* first) {
  if (!*first) {
    output << ",";
  }
  output << "\n    \"" << name << "\": " << value;
  *first = false;
}

void PrintRankMap(std::ostream& output, const std::map<size_t, size_t>& ranks,
                  bool* first) {
  if (!*first) {
    output << ",";
  }
  output << "\n    \"correct_rank\": {";
  bool rank_first = true;
  for (const auto& [rank, count] : ranks) {
    if (!rank_first) {
      output << ",";
    }
    output << "\"" << rank << "\": " << count;
    rank_first = false;
  }
  output << "}";
  *first = false;
}

void PrintSummaryFields(std::ostream& output, const Summary& summary,
                        bool include_gold_metrics) {
  bool first = true;
  PrintNumber(output, "cases", summary.cases, &first);
  PrintNumber(output, "rows_with_candidates", summary.rows_with_candidates,
              &first);
  PrintNumber(output, "total_candidates", summary.total_candidates, &first);
  PrintNumber(output, "auto_candidates", summary.auto_candidates, &first);
  if (include_gold_metrics) {
    PrintNumber(output, "correct_candidates", summary.correct_candidates,
                &first);
    PrintNumber(output, "top1_correct", summary.top1_correct, &first);
    PrintNumber(output, "correct_auto_candidates",
                summary.correct_auto_candidates, &first);
    PrintNumber(output, "correct_rows", summary.correct_rows, &first);
    PrintNumber(output, "correct_auto_rows", summary.correct_auto_rows,
                &first);
    PrintNumber(output, "suggest_cases", summary.suggest_cases, &first);
    PrintNumber(output, "suggest_auto_violation_rows",
                summary.suggest_auto_violation_rows, &first);
    PrintRate(output, "candidate_recall",
              Rate(summary.correct_rows, summary.cases), &first);
    PrintRate(output, "raw_hypothesis_candidate_precision",
              Rate(summary.correct_candidates, summary.total_candidates), &first);
    PrintRate(output, "raw_hypothesis_candidate_recall",
              Rate(summary.correct_rows, summary.cases), &first);
    PrintRate(output, "top1_accuracy",
              Rate(summary.top1_correct, summary.cases), &first);
    PrintRate(output, "raw_hypothesis_top1_accuracy",
              Rate(summary.top1_correct, summary.cases), &first);
    PrintRate(output, "suggest_auto_violation_rate",
              Rate(summary.suggest_auto_violation_rows, summary.suggest_cases),
              &first);
    PrintRankMap(output, summary.correct_rank, &first);
  } else {
    PrintNumber(output, "raw_policy_forbidden_cases",
                summary.raw_policy_forbidden_cases, &first);
    PrintNumber(output, "raw_policy_forbidden_violations",
                summary.raw_policy_forbidden_violations, &first);
    PrintNumber(output, "raw_policy_forbidden_candidates",
                summary.raw_policy_forbidden_candidates, &first);
    PrintNumber(output, "display_policy_forbidden_cases",
                summary.display_policy_forbidden_cases, &first);
    PrintNumber(output, "display_policy_forbidden_violations",
                summary.display_policy_forbidden_violations, &first);
    PrintRate(output, "negative_candidate_rate",
              Rate(summary.rows_with_candidates, summary.cases), &first);
    PrintRate(output, "candidate_false_positive_rate",
              Rate(summary.raw_policy_forbidden_violations,
                   summary.raw_policy_forbidden_cases),
              &first);
    PrintRate(output, "raw_hypothesis_candidate_false_positive_rate",
              Rate(summary.raw_policy_forbidden_violations,
                   summary.raw_policy_forbidden_cases),
              &first);
    PrintRate(output, "raw_policy_false_positive_rate",
              Rate(summary.raw_policy_forbidden_violations,
                   summary.raw_policy_forbidden_cases),
              &first);
    PrintRate(output, "display_policy_raw_hypothesis_violation_rate",
              Rate(summary.display_policy_forbidden_violations,
                   summary.display_policy_forbidden_cases),
              &first);
    PrintRate(output, "auto_false_positive_rate",
              Rate(summary.auto_policy_violations, summary.cases), &first);
  }
}

void PrintBucketMap(std::ostream& output,
                    const std::map<std::string, Summary>& buckets) {
  output << "{";
  bool first = true;
  for (const auto& [name, summary] : buckets) {
    if (!first) {
      output << ",";
    }
    output << "\n      \"" << name << "\": ";
    output << "{";
    PrintSummaryFields(output, summary, true);
    output << "\n    }";
    first = false;
  }
  if (!buckets.empty()) {
    output << "\n  ";
  }
  output << "}";
}

void PrintAggregate(std::ostream& output, const Summary& gold,
                    const Summary& negative, const Summary& auto_gold,
                    const Summary& suggest_gold) {
  output << "{\n"
         << "    \"gold_cases\": " << gold.cases << ",\n"
         << "    \"negative_cases\": " << negative.cases << ",\n"
         << "    \"candidate_precision_numerator\": "
         << gold.correct_candidates << ",\n"
         << "    \"candidate_precision_denominator\": "
         << gold.total_candidates + negative.raw_policy_forbidden_candidates
         << ",\n"
         << "    \"candidate_precision\": "
         << Rate(gold.correct_candidates,
                 gold.total_candidates + negative.raw_policy_forbidden_candidates)
         << ",\n"
         << "    \"gold_candidate_precision_numerator\": "
         << gold.correct_candidates << ",\n"
         << "    \"gold_candidate_precision_denominator\": "
         << gold.total_candidates << ",\n"
         << "    \"raw_hypothesis_candidate_precision\": "
         << Rate(gold.correct_candidates,
                 gold.total_candidates + negative.raw_policy_forbidden_candidates)
         << ",\n"
         << "    \"gold_total_candidates\": " << gold.total_candidates << ",\n"
         << "    \"raw_policy_forbidden_candidates\": "
         << negative.raw_policy_forbidden_candidates << ",\n"
         << "    \"negative_auto_candidates\": "
         << negative.auto_candidates << ",\n"
         << "    \"raw_policy_forbidden_cases\": "
         << negative.raw_policy_forbidden_cases << ",\n"
         << "    \"raw_policy_forbidden_violations\": "
         << negative.raw_policy_forbidden_violations << ",\n"
         << "    \"display_policy_forbidden_cases\": "
         << negative.display_policy_forbidden_cases << ",\n"
         << "    \"display_policy_forbidden_violations\": "
         << negative.display_policy_forbidden_violations << ",\n"
         << "    \"gold_auto_candidates\": " << gold.auto_candidates << ",\n"
         << "    \"gold_auto_rows\": " << gold.auto_rows << ",\n"
         << "    \"auto_candidates\": " << gold.auto_candidates << ",\n"
         << "    \"auto_rows\": " << gold.auto_rows << ",\n"
         << "    \"auto_gold_cases\": " << auto_gold.cases << ",\n"
         << "    \"suggest_gold_cases\": " << suggest_gold.cases << ",\n"
         << "    \"suggest_auto_violation_rows\": "
         << suggest_gold.suggest_auto_violation_rows << ",\n"
         << "    \"suggest_auto_violation_rate\": "
         << Rate(suggest_gold.suggest_auto_violation_rows,
                 suggest_gold.cases)
         << ",\n"
         << "    \"gold_candidate_precision\": "
         << Rate(gold.correct_candidates, gold.total_candidates) << ",\n"
         << "    \"candidate_recall_numerator\": " << gold.correct_rows
         << ",\n"
         << "    \"candidate_recall_denominator\": " << gold.cases
         << ",\n"
         << "    \"candidate_recall\": "
         << Rate(gold.correct_rows, gold.cases)
         << ",\n"
         << "    \"raw_hypothesis_candidate_recall\": "
         << Rate(gold.correct_rows, gold.cases)
         << ",\n"
         << "    \"top1_accuracy\": "
         << Rate(gold.top1_correct, gold.cases) << ",\n"
         << "    \"top1_accuracy_numerator\": " << gold.top1_correct
         << ",\n"
         << "    \"top1_accuracy_denominator\": " << gold.cases << ",\n"
         << "    \"raw_hypothesis_top1_accuracy\": "
         << Rate(gold.top1_correct, gold.cases) << ",\n"
         << "    \"candidate_false_positive_rate\": "
         << Rate(negative.raw_policy_forbidden_violations,
                 negative.raw_policy_forbidden_cases) << ",\n"
         << "    \"raw_hypothesis_candidate_false_positive_rate\": "
         << Rate(negative.raw_policy_forbidden_violations,
                 negative.raw_policy_forbidden_cases) << ",\n"
         << "    \"raw_policy_false_positive_rate\": "
         << Rate(negative.raw_policy_forbidden_violations,
                 negative.raw_policy_forbidden_cases) << ",\n"
         << "    \"display_policy_raw_hypothesis_violation_rate\": "
         << Rate(negative.display_policy_forbidden_violations,
                 negative.display_policy_forbidden_cases) << ",\n"
         << "    \"negative_candidate_rate\": "
         << Rate(negative.rows_with_candidates, negative.cases) << ",\n"
         << "    \"negative_rows_with_candidates\": "
         << negative.rows_with_candidates << ",\n"
         << "    \"negative_top1_wrong_rate\": "
         << Rate(negative.rows_with_candidates, negative.cases) << ",\n"
         << "    \"auto_precision\": "
         << Rate(gold.correct_auto_candidates,
                 gold.auto_candidates + negative.auto_candidates)
         << ",\n"
         << "    \"auto_precision_numerator\": "
         << gold.correct_auto_candidates << ",\n"
         << "    \"auto_precision_denominator\": "
         << gold.auto_candidates + negative.auto_candidates << ",\n"
         << "    \"auto_recall\": "
         << Rate(auto_gold.correct_auto_rows, auto_gold.cases) << ",\n"
         << "    \"auto_recall_numerator\": "
         << auto_gold.correct_auto_rows << ",\n"
         << "    \"auto_recall_denominator\": " << auto_gold.cases << ",\n"
         << "    \"suggest_recall\": "
         << Rate(suggest_gold.correct_rows, suggest_gold.cases) << ",\n"
         << "    \"suggest_recall_numerator\": "
         << suggest_gold.correct_rows << ",\n"
         << "    \"suggest_recall_denominator\": " << suggest_gold.cases
         << ",\n"
         << "    \"suggest_auto_violation_rate_numerator\": "
         << suggest_gold.suggest_auto_violation_rows << ",\n"
         << "    \"suggest_auto_violation_rate_denominator\": "
         << suggest_gold.cases << ",\n"
         << "    \"auto_false_positive_rate\": "
         << Rate(negative.auto_policy_violations, negative.cases) << ",\n"
         << "    \"auto_policy_violations\": "
         << negative.auto_policy_violations << "\n"
         << "\n";
}

void PrintNegativeFeatureMap(std::ostream& output,
                             const std::map<std::string, Summary>& buckets) {
  output << "{";
  bool first = true;
  for (const auto& [name, summary] : buckets) {
    if (!first) {
      output << ",";
    }
    output << "\n      \"" << name << "\": {\n"
           << "        \"cases\": " << summary.cases << ",\n"
           << "        \"rows_with_candidates\": "
           << summary.rows_with_candidates << ",\n"
           << "        \"raw_policy_forbidden_cases\": "
           << summary.raw_policy_forbidden_cases << ",\n"
           << "        \"raw_policy_forbidden_violations\": "
           << summary.raw_policy_forbidden_violations << ",\n"
           << "        \"raw_policy_forbidden_candidates\": "
           << summary.raw_policy_forbidden_candidates << ",\n"
           << "        \"auto_candidates\": " << summary.auto_candidates
           << ",\n"
           << "        \"display_policy_forbidden_cases\": "
           << summary.display_policy_forbidden_cases << ",\n"
           << "        \"display_policy_forbidden_violations\": "
           << summary.display_policy_forbidden_violations << ",\n"
           << "        \"candidate_false_positive_rate\": "
           << Rate(summary.raw_policy_forbidden_violations,
                   summary.raw_policy_forbidden_cases)
           << ",\n"
           << "        \"raw_hypothesis_candidate_false_positive_rate\": "
           << Rate(summary.raw_policy_forbidden_violations,
                   summary.raw_policy_forbidden_cases)
           << ",\n"
           << "        \"raw_policy_false_positive_rate\": "
           << Rate(summary.raw_policy_forbidden_violations,
                   summary.raw_policy_forbidden_cases)
           << ",\n"
           << "        \"display_policy_raw_hypothesis_violation_rate\": "
           << Rate(summary.display_policy_forbidden_violations,
                   summary.display_policy_forbidden_cases)
           << ",\n"
           << "        \"negative_candidate_rate\": "
           << Rate(summary.rows_with_candidates, summary.cases) << ",\n"
           << "        \"negative_rows_with_candidates\": "
           << summary.rows_with_candidates << ",\n"
           << "        \"negative_top1_wrong_rate\": "
           << Rate(summary.rows_with_candidates, summary.cases) << ",\n"
           << "        \"auto_false_positive_rate\": "
           << Rate(summary.auto_policy_violations, summary.cases) << ",\n"
           << "        \"auto_policy_violations\": "
           << summary.auto_policy_violations << "\n"
           << "      }";
    first = false;
  }
  if (!buckets.empty()) {
    output << "\n    ";
  }
  output << "}";
}

#if defined(MOZKEY_ENGINE_METRICS)
struct EngineWindowSummary {
  size_t cases = 0;
  size_t correct = 0;
  size_t top1_correct = 0;
  size_t topk_correct = 0;
  size_t kana_cases = 0;
  size_t kana_correct = 0;
  size_t kana_top1_correct = 0;
  size_t kana_topk_correct = 0;
  size_t negative_cases = 0;
  size_t negative_violations = 0;
  size_t kana_negative_cases = 0;
  size_t kana_negative_violations = 0;
};

bool IsTypingCorrectionValue(const std::vector<std::string>& values,
                             const absl::string_view value) {
  for (const std::string& candidate : values) {
    if (candidate == value) {
      return true;
    }
  }
  return false;
}

struct EngineWindowObservation {
  bool has_visible_typing_correction = false;
  bool expected_found = false;
  bool expected_top1 = false;
};

bool InsertKanaKeyTrace(composer::Composer* composer,
                        const absl::string_view key_codes,
                        const absl::string_view key_strings) {
  for (const commands::KeyEvent& event : MakeEvents(key_codes, key_strings)) {
    if (!composer->InsertCharacterKeyEvent(event)) {
      return false;
    }
  }
  return true;
}

EngineWindowObservation ObserveEngineWindow(
    composer::Composer* composer, const std::shared_ptr<::mozc::MockConverter>&
                                     mock_converter,
    const std::shared_ptr<commands::Request>& request,
    const std::shared_ptr<config::Config>& config, absl::string_view typed_raw,
    absl::string_view typed_key_strings, absl::string_view expected_reading) {
  EngineWindowObservation observation;
  composer->EditErase();
  if (typed_key_strings.empty()) {
    composer->InsertCharacter(std::string(typed_raw));
  } else if (!InsertKanaKeyTrace(composer, typed_raw, typed_key_strings)) {
    return observation;
  }
  engine::EngineConverter converter(mock_converter, request, config);
  if (!converter.Convert(*composer)) {
    return observation;
  }
  converter.SetCandidateListVisible(true);
  commands::Output output;
  converter.FillOutput(*composer, &output);
  if (!output.has_candidate_window()) {
    return observation;
  }
  const std::vector<std::string> typing_values =
      converter.TypingCorrectionCandidateValues();
  size_t typing_rank = 0;
  for (int index = 0; index < output.candidate_window().candidate_size();
       ++index) {
    const auto& candidate = output.candidate_window().candidate(index);
    if (!IsTypingCorrectionValue(typing_values, candidate.value())) {
      continue;
    }
    observation.has_visible_typing_correction = true;
    if (candidate.value() == expected_reading) {
      observation.expected_found = true;
      if (typing_rank == 0) {
        observation.expected_top1 = true;
      }
    }
    ++typing_rank;
  }
  return observation;
}

bool EvaluateEngineWindowCase(
    composer::Composer* composer, const std::shared_ptr<::mozc::MockConverter>&
                                     mock_converter,
    const std::shared_ptr<commands::Request>& request,
    const std::shared_ptr<config::Config>& config, absl::string_view typed_raw,
    absl::string_view typed_key_strings, absl::string_view expected_reading,
    bool* top1_correct, bool* topk_correct) {
  const EngineWindowObservation observation = ObserveEngineWindow(
      composer, mock_converter, request, config, typed_raw, typed_key_strings,
      expected_reading);
  if (top1_correct != nullptr) {
    *top1_correct = observation.expected_top1;
  }
  if (topk_correct != nullptr) {
    *topk_correct = observation.expected_found;
  }
  return observation.expected_found;
}

bool EvaluateEngineWindowNegative(
    composer::Composer* composer, const std::shared_ptr<::mozc::MockConverter>&
                                     mock_converter,
    const std::shared_ptr<commands::Request>& request,
    const std::shared_ptr<config::Config>& config, absl::string_view typed_raw,
    absl::string_view typed_key_strings) {
  return ObserveEngineWindow(composer, mock_converter, request, config, typed_raw,
                             typed_key_strings, "")
      .has_visible_typing_correction;
}

std::shared_ptr<::mozc::MockConverter> MakeEngineMetricsMockConverter() {
  auto mock_converter = std::make_shared<::mozc::MockConverter>();
  EXPECT_CALL(*mock_converter, StartConversion(::testing::_, ::testing::_))
      .WillRepeatedly(::testing::Invoke(
          [](const ::mozc::ConversionRequest& conversion_request,
             ::mozc::Segments* segments) {
            segments->Clear();
            ::mozc::Segment* segment = segments->add_segment();
            segment->set_key(conversion_request.key());
            ::mozc::converter::Candidate* candidate =
                segment->add_candidate();
            candidate->key = std::string(conversion_request.key());
            candidate->content_key = candidate->key;
            candidate->value = candidate->key;
            candidate->content_value = candidate->value;
            candidate->cost = 100;
            candidate->wcost = 100;
            return true;
          }));
  return mock_converter;
}

EngineWindowSummary EvaluateEngineE2E() {
  EngineWindowSummary summary;
  summary.cases = kGeneratedRomanHoldoutCaseCount;
  summary.kana_cases = kGeneratedKanaHoldoutCaseCount;
  for (const KanaNegativeCase& test_case : kGeneratedKanaNegativeCases) {
    if (test_case.display_forbidden) {
      ++summary.kana_negative_cases;
    }
  }

  auto config = std::make_shared<config::Config>();
  config->set_use_typing_correction(true);
  auto request = std::make_shared<commands::Request>();
  auto table = std::make_shared<composer::Table>();
  if (!table->InitializeWithRequestAndConfig(*request, *config)) {
    summary.negative_violations = summary.negative_cases;
    summary.kana_negative_violations = summary.kana_negative_cases;
    return summary;
  }
  composer::Composer composer(table, *request, *config);

  auto mock_converter = MakeEngineMetricsMockConverter();

  for (const RomanHoldoutCase& test_case : kGeneratedRomanHoldoutCases) {
    bool top1 = false;
    bool topk = false;
    if (EvaluateEngineWindowCase(
            &composer, mock_converter, request, config, test_case.typed_raw,
            "", test_case.corrected_reading, &top1, &topk)) {
      ++summary.correct;
    }
    if (top1) {
      ++summary.top1_correct;
    }
    if (topk) {
      ++summary.topk_correct;
    }
  }
  for (const RomanNegativeCase& test_case : kGeneratedRomanNegativeCases) {
    if (!test_case.display_forbidden) {
      continue;
    }
    ++summary.negative_cases;
    if (EvaluateEngineWindowNegative(&composer, mock_converter, request, config,
                                     test_case.typed_raw, "")) {
      ++summary.negative_violations;
    }
  }

  auto kana_config = std::make_shared<config::Config>();
  kana_config->set_preedit_method(config::Config::KANA);
  kana_config->set_use_typing_correction(true);
  auto kana_request = std::make_shared<commands::Request>();
  auto kana_table = std::make_shared<composer::Table>();
  if (!kana_table->InitializeWithRequestAndConfig(*kana_request,
                                                   *kana_config)) {
    summary.kana_negative_violations = summary.kana_negative_cases;
    return summary;
  }
  composer::Composer kana_composer(kana_table, *kana_request, *kana_config);
  auto kana_mock_converter = MakeEngineMetricsMockConverter();
  for (const KanaHoldoutCase& test_case : kGeneratedKanaHoldoutCases) {
    bool top1 = false;
    bool topk = false;
    if (EvaluateEngineWindowCase(
            &kana_composer, kana_mock_converter, kana_request, kana_config,
            test_case.typed_key_codes, test_case.typed_key_strings,
            test_case.corrected_key_strings, &top1, &topk)) {
      ++summary.kana_correct;
    }
    if (top1) {
      ++summary.kana_top1_correct;
    }
    if (topk) {
      ++summary.kana_topk_correct;
    }
  }
  for (const KanaNegativeCase& test_case : kGeneratedKanaNegativeCases) {
    if (!test_case.display_forbidden) {
      continue;
    }
    if (EvaluateEngineWindowNegative(
            &kana_composer, kana_mock_converter, kana_request, kana_config,
            test_case.typed_raw, test_case.typed_key_string)) {
      ++summary.kana_negative_violations;
    }
  }
  return summary;
}
#endif

int Run() {
  std::cout << std::fixed << std::setprecision(6);
  Summary roman_gold;
  Summary roman_gold_auto;
  Summary roman_gold_suggest;
  Summary roman_negative;
  std::map<std::string, Summary> roman_by_operation;
  std::map<std::string, Summary> roman_by_length;
  std::map<std::string, Summary> roman_by_position;
  std::map<std::string, Summary> roman_by_lexical;
  std::map<std::string, Summary> roman_by_feature;
  std::map<std::string, Summary> roman_negative_by_length;
  std::map<std::string, Summary> roman_negative_by_position;
  std::map<std::string, Summary> roman_negative_by_lexical;
  std::map<std::string, Summary> roman_negative_by_feature;
  const RomanTypingCorrector roman_corrector;
  for (const RomanGoldCase& test_case : kGeneratedRomanGoldCases) {
    const std::vector<Hypothesis> hypotheses =
        GenerateRomanEvaluationHypotheses(roman_corrector, test_case.typed_raw);
    const auto matches =
        [expected = std::string(test_case.corrected_raw)](
            const Hypothesis& hypothesis) {
          return hypothesis.corrected_raw == expected;
        };
    RecordRankedGold(
        &roman_gold, &roman_by_operation, &roman_by_length,
        &roman_by_position, &roman_by_lexical, &roman_by_feature,
        std::string(MetricOperationName(test_case.operation)),
        StratumValue(test_case.stratum, "length"),
        StratumValue(test_case.stratum, "position"),
        StratumValue(test_case.stratum, "lexical"),
        StratumValue(test_case.stratum, "feature"), hypotheses, matches,
        test_case.auto_applicable);
    if (test_case.auto_applicable) {
      RecordGoldSummary(&roman_gold_auto, hypotheses, matches, true);
    } else {
      RecordGoldSummary(&roman_gold_suggest, hypotheses, matches, false);
    }
  }
  for (const RomanNegativeCase& test_case : kGeneratedRomanNegativeCases) {
    const std::vector<Hypothesis> hypotheses =
        GenerateRomanEvaluationHypotheses(roman_corrector, test_case.typed_raw);
    RecordRankedNegative(
        &roman_negative, &roman_negative_by_length,
        &roman_negative_by_position, &roman_negative_by_lexical,
        &roman_negative_by_feature, StratumValue(test_case.stratum, "length"),
        StratumValue(test_case.stratum, "position"),
        StratumValue(test_case.stratum, "lexical"),
        StratumValue(test_case.stratum, "feature"), hypotheses,
        test_case.raw_forbidden, test_case.display_forbidden,
        test_case.auto_forbidden);
  }

  Summary kana_gold;
  Summary kana_gold_auto;
  Summary kana_gold_suggest;
  Summary kana_negative;
  std::map<std::string, Summary> kana_by_operation;
  std::map<std::string, Summary> kana_by_length;
  std::map<std::string, Summary> kana_by_position;
  std::map<std::string, Summary> kana_by_lexical;
  std::map<std::string, Summary> kana_by_feature;
  std::map<std::string, Summary> kana_negative_by_length;
  std::map<std::string, Summary> kana_negative_by_position;
  std::map<std::string, Summary> kana_negative_by_lexical;
  std::map<std::string, Summary> kana_negative_by_feature;
  const KanaTypingCorrector kana_corrector;
  for (const KanaGoldCase& test_case : kGeneratedKanaEvaluationCases) {
    const std::vector<commands::KeyEvent> events =
        MakeEvents(test_case.typed_raw, test_case.typed_key_string);
    const std::vector<Hypothesis> hypotheses = GenerateKanaEvaluationHypotheses(
        kana_corrector, events, test_case.typed_key_string);
    const auto matches = [&test_case](const Hypothesis& hypothesis) {
      return SameTrace(hypothesis.corrected_key_events,
                       test_case.corrected_raw,
                       test_case.corrected_key_string);
    };
    RecordRankedGold(
        &kana_gold, &kana_by_operation, &kana_by_length, &kana_by_position,
        &kana_by_lexical, &kana_by_feature,
        std::string(MetricOperationName(test_case.operation)),
        StratumValue(test_case.stratum, "length"),
        StratumValue(test_case.stratum, "position"),
        StratumValue(test_case.stratum, "lexical"),
        StratumValue(test_case.stratum, "feature"), hypotheses, matches,
        test_case.auto_applicable);
    if (test_case.auto_applicable) {
      RecordGoldSummary(&kana_gold_auto, hypotheses, matches, true);
    } else {
      RecordGoldSummary(&kana_gold_suggest, hypotheses, matches, false);
    }
  }
  for (const KanaNegativeCase& test_case : kGeneratedKanaNegativeCases) {
    const std::vector<commands::KeyEvent> events =
        MakeEvents(test_case.typed_raw, test_case.typed_key_string);
    const std::vector<Hypothesis> hypotheses = GenerateKanaEvaluationHypotheses(
        kana_corrector, events, test_case.typed_key_string);
    RecordRankedNegative(
        &kana_negative, &kana_negative_by_length, &kana_negative_by_position,
        &kana_negative_by_lexical, &kana_negative_by_feature,
        StratumValue(test_case.stratum, "length"),
        StratumValue(test_case.stratum, "position"),
        StratumValue(test_case.stratum, "lexical"),
        StratumValue(test_case.stratum, "feature"), hypotheses,
        test_case.raw_forbidden, test_case.display_forbidden,
        test_case.auto_forbidden);
  }

  auto table = std::make_shared<composer::Table>();
  commands::Request request;
  config::Config config;
  if (!table->InitializeWithRequestAndConfig(request, config)) {
    std::cerr << "failed to initialize Composer table\n";
    return 1;
  }
  composer::Composer composer(table, request, config);
  RomanInputGateContext context;
  context.feature_enabled = true;
  size_t composer_correct = 0;
  for (const RomanHoldoutCase& test_case : kGeneratedRomanHoldoutCases) {
    composer.EditErase();
    composer.InsertCharacter(std::string(test_case.typed_raw));
    const std::vector<Hypothesis> hypotheses =
        GenerateRomanCorrectionHypotheses(composer, context);
    for (const Hypothesis& hypothesis : hypotheses) {
      if (hypothesis.corrected_raw == test_case.corrected_raw &&
          hypothesis.corrected_reading == test_case.corrected_reading) {
        ++composer_correct;
        break;
      }
    }
  }

#if defined(MOZKEY_ENGINE_METRICS)
  const EngineWindowSummary engine_summary = EvaluateEngineE2E();
#endif

  const size_t total_cases = kGeneratedRomanGoldCaseCount +
                             kGeneratedRomanNegativeCaseCount +
                             kGeneratedKanaEvaluationCaseCount +
                             kGeneratedKanaNegativeCaseCount +
                             kGeneratedRomanHoldoutCaseCount +
                             kGeneratedKanaHoldoutCaseCount;
  std::cout << "{\n"
            << "  \"corpus_cases\": {\n"
            << "    \"total\": " << total_cases << ",\n"
            << "    \"roman_gold\": " << kGeneratedRomanGoldCaseCount
            << ",\n"
            << "    \"roman_negative\": "
            << kGeneratedRomanNegativeCaseCount << ",\n"
            << "    \"kana_gold\": " << kGeneratedKanaEvaluationCaseCount
            << ",\n"
            << "    \"kana_negative\": "
            << kGeneratedKanaNegativeCaseCount << ",\n"
            << "    \"roman_holdout\": "
            << kGeneratedRomanHoldoutCaseCount << ",\n"
            << "    \"kana_holdout\": " << kGeneratedKanaHoldoutCaseCount
            << "\n  },\n"
            << "  \"roman\": ";
  PrintAggregate(std::cout, roman_gold, roman_negative, roman_gold_auto,
                 roman_gold_suggest);
  std::cout << ",\n    \"by_operation\": ";
  PrintBucketMap(std::cout, roman_by_operation);
  std::cout << ",\n    \"by_length\": ";
  PrintBucketMap(std::cout, roman_by_length);
  std::cout << ",\n    \"by_position\": ";
  PrintBucketMap(std::cout, roman_by_position);
  std::cout << ",\n    \"by_lexical\": ";
  PrintBucketMap(std::cout, roman_by_lexical);
  std::cout << ",\n    \"by_feature\": ";
  PrintBucketMap(std::cout, roman_by_feature);
  std::cout << ",\n    \"negative_by_feature\": ";
  PrintNegativeFeatureMap(std::cout, roman_negative_by_feature);
  std::cout << ",\n    \"negative_by_length\": ";
  PrintNegativeFeatureMap(std::cout, roman_negative_by_length);
  std::cout << ",\n    \"negative_by_position\": ";
  PrintNegativeFeatureMap(std::cout, roman_negative_by_position);
  std::cout << ",\n    \"negative_by_lexical\": ";
  PrintNegativeFeatureMap(std::cout, roman_negative_by_lexical);
  std::cout << "\n  },\n  \"kana\": ";
  PrintAggregate(std::cout, kana_gold, kana_negative, kana_gold_auto,
                 kana_gold_suggest);
  std::cout << ",\n    \"by_operation\": ";
  PrintBucketMap(std::cout, kana_by_operation);
  std::cout << ",\n    \"by_length\": ";
  PrintBucketMap(std::cout, kana_by_length);
  std::cout << ",\n    \"by_position\": ";
  PrintBucketMap(std::cout, kana_by_position);
  std::cout << ",\n    \"by_lexical\": ";
  PrintBucketMap(std::cout, kana_by_lexical);
  std::cout << ",\n    \"by_feature\": ";
  PrintBucketMap(std::cout, kana_by_feature);
  std::cout << ",\n    \"negative_by_feature\": ";
  PrintNegativeFeatureMap(std::cout, kana_negative_by_feature);
  std::cout << ",\n    \"negative_by_length\": ";
  PrintNegativeFeatureMap(std::cout, kana_negative_by_length);
  std::cout << ",\n    \"negative_by_position\": ";
  PrintNegativeFeatureMap(std::cout, kana_negative_by_position);
  std::cout << ",\n    \"negative_by_lexical\": ";
  PrintNegativeFeatureMap(std::cout, kana_negative_by_lexical);
  std::cout << "\n  },\n"
            << "  \"composer_replay\": {\n"
            << "    \"cases\": " << kGeneratedRomanHoldoutCaseCount
            << ",\n"
            << "    \"correct\": " << composer_correct << ",\n"
            << "    \"recall\": "
            << Rate(composer_correct, kGeneratedRomanHoldoutCaseCount)
            << "\n  },\n"
            << "  \"engine_e2e\": {\n"
            << "    \"cases\": " << kGeneratedRomanHoldoutCaseCount
            << ",\n"
#if defined(MOZKEY_ENGINE_METRICS)
            << "    \"engine_e2e_recall\": "
            << Rate(engine_summary.correct, engine_summary.cases) << ",\n"
            << "    \"engine_correct\": " << engine_summary.correct << ",\n"
            << "    \"candidate_window_top1_accuracy\": "
            << Rate(engine_summary.top1_correct, engine_summary.cases) << ",\n"
            << "    \"candidate_window_topk_accuracy\": "
            << Rate(engine_summary.topk_correct, engine_summary.cases) << ",\n"
            << "    \"candidate_window_top1_correct\": "
            << engine_summary.top1_correct << ",\n"
            << "    \"candidate_window_topk_correct\": "
            << engine_summary.topk_correct << ",\n"
            << "    \"kana_cases\": " << engine_summary.kana_cases << ",\n"
            << "    \"kana_engine_e2e_recall\": "
            << Rate(engine_summary.kana_correct, engine_summary.kana_cases)
            << ",\n"
            << "    \"kana_engine_correct\": "
            << engine_summary.kana_correct << ",\n"
            << "    \"candidate_window_kana_top1_accuracy\": "
            << Rate(engine_summary.kana_top1_correct,
                    engine_summary.kana_cases) << ",\n"
            << "    \"candidate_window_kana_top1_correct\": "
            << engine_summary.kana_top1_correct << ",\n"
            << "    \"candidate_window_kana_topk_accuracy\": "
            << Rate(engine_summary.kana_topk_correct,
                    engine_summary.kana_cases) << ",\n"
            << "    \"candidate_window_kana_topk_correct\": "
            << engine_summary.kana_topk_correct << ",\n"
            << "    \"candidate_window_negative_cases\": "
            << engine_summary.negative_cases << ",\n"
            << "    \"candidate_window_negative_violations\": "
            << engine_summary.negative_violations << ",\n"
            << "    \"candidate_window_negative_false_positive_rate\": "
            << Rate(engine_summary.negative_violations,
                    engine_summary.negative_cases) << ",\n"
            << "    \"candidate_window_kana_negative_cases\": "
            << engine_summary.kana_negative_cases << ",\n"
            << "    \"candidate_window_kana_negative_violations\": "
            << engine_summary.kana_negative_violations << ",\n"
            << "    \"candidate_window_kana_negative_false_positive_rate\": "
            << Rate(engine_summary.kana_negative_violations,
                    engine_summary.kana_negative_cases) << ",\n"
            << "    \"validated_by\": \"//engine:evaluate_typing_correction_metrics\"\n"
#else
            << "    \"engine_e2e_recall\": null,\n"
            << "    \"engine_correct\": 0,\n"
            << "    \"candidate_window_top1_accuracy\": null,\n"
            << "    \"candidate_window_topk_accuracy\": null,\n"
            << "    \"candidate_window_top1_correct\": 0,\n"
            << "    \"candidate_window_topk_correct\": 0,\n"
            << "    \"kana_cases\": 0,\n"
            << "    \"kana_engine_e2e_recall\": null,\n"
            << "    \"kana_engine_correct\": 0,\n"
            << "    \"candidate_window_kana_top1_accuracy\": null,\n"
            << "    \"candidate_window_kana_top1_correct\": 0,\n"
            << "    \"candidate_window_kana_topk_accuracy\": null,\n"
            << "    \"candidate_window_kana_topk_correct\": 0,\n"
            << "    \"candidate_window_negative_cases\": 0,\n"
            << "    \"candidate_window_negative_violations\": 0,\n"
            << "    \"candidate_window_negative_false_positive_rate\": null,\n"
            << "    \"candidate_window_kana_negative_cases\": 0,\n"
            << "    \"candidate_window_kana_negative_violations\": 0,\n"
            << "    \"candidate_window_kana_negative_false_positive_rate\": null,\n"
            << "    \"validated_by\": \"//engine:engine_converter_test\"\n"
#endif
            << "  }\n"
            << "}\n";
  return 0;
}

}  // namespace
}  // namespace mozc::typing_correction

int main() { return mozc::typing_correction::Run(); }
