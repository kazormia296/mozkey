// Copyright 2026 Grimodex contributors.

#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include "composer/table.h"
#include "protocol/commands.pb.h"
#include "protocol/config.pb.h"
#include "typing_correction/composer_replayer.h"
#include "typing_correction/generated_roman_rules.h"

int main(int argc, char** argv) {
  std::string mode = "roman";
  std::string raw;
  for (int i = 1; i < argc; ++i) {
    const std::string argument = argv[i];
    if (argument.starts_with("--mode=")) {
      mode = argument.substr(7);
    } else if (argument.starts_with("--raw=")) {
      raw = argument.substr(6);
    }
  }

  if (mode != "roman" || raw.empty()) {
    std::cerr << "usage: inspect_typing_correction --mode=roman --raw=TEXT\n";
    return 2;
  }

  mozc::commands::Request request;
  mozc::config::Config config;
  auto table = std::make_shared<mozc::composer::Table>();
  if (!table->InitializeWithRequestAndConfig(request, config)) {
    std::cerr << "failed to initialize Roman table\n";
    return 1;
  }
  mozc::composer::Composer composer(table, request, config);
  composer.InsertCharacter(raw);
  mozc::typing_correction::RomanInputGateContext context;
  context.feature_enabled = true;
  const std::vector<mozc::typing_correction::Hypothesis> hypotheses =
      mozc::typing_correction::GenerateRomanCorrectionHypotheses(composer,
                                                                  context);

  std::cout << "corpus_version="
            << mozc::typing_correction::kTypingCorrectionCorpusVersion
            << " corpus_sha256="
            << mozc::typing_correction::kTypingCorrectionCorpusSha256 << '\n';
  std::cout << "raw=" << composer.GetRawString()
            << " reading=" << composer.GetQueryForConversion() << '\n';
  for (const mozc::typing_correction::Hypothesis& hypothesis : hypotheses) {
    const mozc::typing_correction::Edit& edit = hypothesis.edits.front();
    std::cout << "corrected_raw=" << hypothesis.corrected_raw
              << " corrected_reading=" << hypothesis.corrected_reading
              << " operation="
              << mozc::typing_correction::OperationName(edit.operation)
              << " cost=" << hypothesis.edit_cost
              << " auto=" << (hypothesis.auto_applicable ? "true" : "false")
              << " rule=" << edit.rule_id << '\n';
  }
  return 0;
}
