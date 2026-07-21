// Copyright 2026 Grimodex contributors.

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

#include "typing_correction/roman_typing_corrector.h"

int main(int argc, char** argv) {
  size_t iterations = 10000;
  if (argc == 2) {
    iterations = std::strtoull(argv[1], nullptr, 10);
  }
  if (iterations == 0) {
    return 2;
  }

  const mozc::typing_correction::RomanTypingCorrector corrector;
  const std::vector<std::string> inputs = {"kudasia", "onegia", "shimaiashita",
                                           "arigatouu", "ohayouu"};
  std::vector<double> samples;
  samples.reserve(iterations);
  size_t hypotheses = 0;
  for (size_t iteration = 0; iteration < iterations; ++iteration) {
    const auto begin = std::chrono::steady_clock::now();
    for (const std::string& input : inputs) {
      hypotheses += corrector.Generate(input).size();
    }
    const auto end = std::chrono::steady_clock::now();
    samples.push_back(std::chrono::duration<double, std::micro>(end - begin)
                          .count());
  }
  std::sort(samples.begin(), samples.end());
  const size_t p95_index = (samples.size() * 95) / 100;
  const double p50_per_input = samples[samples.size() / 2] / inputs.size();
  const double p95_per_input =
      samples[std::min(p95_index, samples.size() - 1)] / inputs.size();
  std::cout << "iterations=" << iterations << " hypotheses=" << hypotheses
            << " p50_us=" << samples[samples.size() / 2]
            << " p95_us=" << samples[std::min(p95_index, samples.size() - 1)]
            << " p50_us_per_input=" << p50_per_input
            << " p95_us_per_input=" << p95_per_input
            << '\n';
  return 0;
}
