// Copyright 2026 The Mozkey Authors

#include "win32/tip/tip_grimodex_client_context.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

#include "grimodex/client_context.h"
#include "protocol/commands.pb.h"

namespace mozc::win32::tsf {
namespace {

constexpr std::string_view kTsfFrontend = "tsf";

char AsciiLower(char value) {
  if ('A' <= value && value <= 'Z') {
    return value - 'A' + 'a';
  }
  return value;
}

}  // namespace

std::string NormalizeExecutableBasename(std::string_view executable_path) {
  if (executable_path.empty()) {
    return {};
  }

  const std::size_t separator = executable_path.find_last_of("/\\");
  std::string basename(executable_path.substr(
      separator == std::string_view::npos ? 0 : separator + 1));
  std::transform(basename.begin(), basename.end(), basename.begin(),
                 AsciiLower);
  constexpr std::string_view kExecutableSuffix = ".exe";
  if (basename.ends_with(kExecutableSuffix)) {
    basename.resize(basename.size() - kExecutableSuffix.size());
  }
  return basename;
}

void TipGrimodexDomainTracker::OnFocusChanged() {
  focus_epoch_ = grimodex::AdvanceFocusEpoch(focus_epoch_);
}

uint64_t TipGrimodexDomainTracker::Observe(std::string_view program,
                                           bool secure_input) {
  if (observed_ && (program_ != program || secure_input_ != secure_input)) {
    focus_epoch_ = grimodex::AdvanceFocusEpoch(focus_epoch_);
  }
  program_ = program;
  secure_input_ = secure_input;
  observed_ = true;
  return focus_epoch_;
}

commands::Context BuildTsfClientContext(
    std::string_view program, bool secure_input, uint64_t focus_epoch,
    const grimodex::SurroundingTextProvider& surrounding_text_provider) {
  return grimodex::BuildClientContext(program, kTsfFrontend, secure_input,
                                      focus_epoch, surrounding_text_provider);
}

}  // namespace mozc::win32::tsf
