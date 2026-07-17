// Copyright 2026 The Mozkey Authors

#include <iostream>
#include <string>
#include <utility>

#include "absl/status/status.h"
#include "base/environ.h"
#include "grimodex/protocol_v1.h"
#include "unix/fcitx5/grimodex_consumer_registrar.h"

int main(int argc, char **argv) {
  std::string root;
  if (argc == 1) {
    root = mozc::grimodex::ResolveProtocolV1Root(
        mozc::Environ::GetEnv("GRIMODEX_IME_ROOT"),
        mozc::Environ::GetEnv("XDG_DATA_HOME"),
        mozc::Environ::GetEnv("HOME"));
  } else if (argc == 3 && std::string(argv[1]) == "--root") {
    root = argv[2];
  } else {
    std::cerr << "usage: " << argv[0] << " [--root /absolute/ime/root]\n";
    return 2;
  }

  mozc::fcitx5::GrimodexConsumerRegistrar registrar(std::move(root));
  const absl::Status status = registrar.Unregister();
  if (!status.ok()) {
    std::cerr << "failed to unregister Mozkey Grimodex consumer: " << status
              << "\n";
    return 1;
  }
  std::cout << "Mozkey Grimodex consumer handshake removed; snapshots and "
               "other consumers preserved\n";
  return 0;
}
