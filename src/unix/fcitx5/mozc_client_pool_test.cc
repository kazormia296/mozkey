// Copyright 2026 The Mozkey Authors
// Licensed under the Apache License, Version 2.0.

#include "unix/fcitx5/mozc_client_pool.h"

#include <fcitx/inputcontext.h>

#include <memory>
#include <string>
#include <string_view>

#include "protocol/commands.pb.h"
#include "protocol/config.pb.h"
#include "testing/gunit.h"
#include "unix/fcitx5/mozc_client_interface.h"

namespace fcitx {
namespace {

class PoolTestClient final : public MozcClientInterface {
 public:
  explicit PoolTestClient(int identity) : identity(identity) {}

  bool EnsureConnection() override { return true; }
  bool SendKeyWithContext(const mozc::commands::KeyEvent&,
                          const mozc::commands::Context&,
                          mozc::commands::Output*) override {
    return true;
  }
  bool SendCommandWithContext(const mozc::commands::SessionCommand&,
                              const mozc::commands::Context&,
                              mozc::commands::Output*) override {
    return true;
  }
  bool IsDirectModeCommand(const mozc::commands::KeyEvent&) const override {
    return false;
  }
  bool GetConfig(mozc::config::Config*) override { return true; }
  void set_client_capability(const mozc::commands::Capability&) override {}
  bool SyncData() override { return true; }
  bool LaunchTool(const std::string&, std::string_view) override { return true; }
  bool LaunchToolWithProtoBuf(const mozc::commands::Output&) override {
    return true;
  }

  const int identity;
};

TEST(MozcClientPoolTest, InputContextUuidIsTheOnlySharingDomain) {
  int next_identity = 0;
  MozcClientPool pool([&next_identity] {
    return std::make_unique<PoolTestClient>(++next_identity);
  });

  ICUUID first_uuid{};
  first_uuid[0] = 1;
  ICUUID second_uuid{};
  second_uuid[0] = 2;

  const auto first = pool.requestClient(first_uuid);
  const auto first_again = pool.requestClient(first_uuid);
  const auto second = pool.requestClient(second_uuid);

  ASSERT_NE(first, nullptr);
  ASSERT_NE(second, nullptr);
  EXPECT_EQ(first.get(), first_again.get());
  EXPECT_NE(first.get(), second.get());
  EXPECT_EQ(next_identity, 2);
}

TEST(MozcClientPoolTest, ReleasedInputContextDoesNotRetainItsSession) {
  int next_identity = 0;
  MozcClientPool pool([&next_identity] {
    return std::make_unique<PoolTestClient>(++next_identity);
  });
  ICUUID uuid{};
  uuid[0] = 9;

  std::weak_ptr<MozcClientInterface> released;
  {
    const auto first = pool.requestClient(uuid);
    released = first;
    ASSERT_NE(first, nullptr);
  }
  EXPECT_TRUE(released.expired());

  const auto replacement = pool.requestClient(uuid);
  ASSERT_NE(replacement, nullptr);
  EXPECT_EQ(next_identity, 2);
}

}  // namespace
}  // namespace fcitx
