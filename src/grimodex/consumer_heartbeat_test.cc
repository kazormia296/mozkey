// Copyright 2026 The Mozkey Authors

#include "grimodex/consumer_heartbeat.h"

#include <string>
#include <vector>

#include "absl/status/status.h"
#include "absl/time/time.h"
#include "grimodex/consumer_file_registrar.h"
#include "grimodex/consumer_handshake.h"
#include "grimodex/desktop_consumer_heartbeat.h"
#include "testing/gunit.h"

namespace mozc::grimodex {
namespace {

class FakeRegistrar final : public ConsumerFileRegistrar {
 public:
  absl::Status Refresh(const ConsumerHandshake &handshake) const override {
    refreshes.push_back(handshake);
    return refresh_status;
  }

  absl::Status Unregister(absl::string_view consumer_id) const override {
    unregisters.emplace_back(consumer_id);
    return unregister_status;
  }

  mutable std::vector<ConsumerHandshake> refreshes;
  mutable std::vector<std::string> unregisters;
  absl::Status refresh_status = absl::OkStatus();
  absl::Status unregister_status = absl::OkStatus();
};

class FakeClock final {
 public:
  explicit FakeClock(absl::Time now) : now_(now) {}

  absl::Time Now() const { return now_; }
  void Set(absl::Time now) { now_ = now; }

 private:
  absl::Time now_;
};

ConsumerHandshake WindowsMetadata(bool zenz_available = true) {
  return ConsumerHandshake{
      .consumer_id = std::string(kTsfConsumerId),
      .name = "Mozkey IbG for Grimodex on Windows",
      .version = "v0.7.7",
      .platform = "windows",
      .last_seen = "caller-supplied-time-must-be-ignored",
      .capabilities =
          ConsumerCapabilities{
              .profile = true,
              .dynamic_dictionary = true,
              .zenzai_v3_conditions = zenz_available,
              .application_scoping = true,
          },
  };
}

TEST(ConsumerHeartbeatTest, PublishesCanonicalUtcMillisecondsImmediately) {
  FakeRegistrar registrar;
  FakeClock clock(absl::FromUnixSeconds(0) + absl::Microseconds(456789));
  ConsumerHeartbeat heartbeat(registrar, WindowsMetadata(),
                              [&clock] { return clock.Now(); });

  ASSERT_TRUE(heartbeat.RefreshNow().ok());
  ASSERT_EQ(registrar.refreshes.size(), 1);
  const ConsumerHandshake &published = registrar.refreshes.front();
  EXPECT_EQ(published.last_seen, "1970-01-01T00:00:00.456Z");
  EXPECT_EQ(published.consumer_id, kTsfConsumerId);
  EXPECT_EQ(published.name, "Mozkey IbG for Grimodex on Windows");
  EXPECT_EQ(published.version, "v0.7.7");
  EXPECT_EQ(published.platform, "windows");
  EXPECT_TRUE(published.capabilities.profile);
  EXPECT_TRUE(published.capabilities.dynamic_dictionary);
  EXPECT_TRUE(published.capabilities.zenzai_v3_conditions);
  EXPECT_TRUE(published.capabilities.application_scoping);
}

TEST(ConsumerHeartbeatTest, RefreshesAtNineHundredSecondsAndOnRollback) {
  FakeRegistrar registrar;
  const absl::Time initial = absl::FromUnixSeconds(1'000'000);
  FakeClock clock(initial);
  ConsumerHeartbeat heartbeat(registrar, WindowsMetadata(),
                              [&clock] { return clock.Now(); });

  ASSERT_TRUE(heartbeat.RefreshIfDue().ok());
  ASSERT_EQ(registrar.refreshes.size(), 1);

  clock.Set(initial + absl::Seconds(899));
  EXPECT_TRUE(heartbeat.RefreshIfDue().ok());
  EXPECT_EQ(registrar.refreshes.size(), 1);

  clock.Set(initial + kConsumerRefreshInterval);
  EXPECT_TRUE(heartbeat.RefreshIfDue().ok());
  ASSERT_EQ(registrar.refreshes.size(), 2);
  EXPECT_EQ(registrar.refreshes.back().last_seen,
            "1970-01-12T14:01:40.000Z");

  clock.Set(initial - absl::Seconds(1));
  EXPECT_TRUE(heartbeat.RefreshIfDue().ok());
  EXPECT_EQ(registrar.refreshes.size(), 3);
}

TEST(ConsumerHeartbeatTest, FailedRefreshDoesNotSuppressRetry) {
  FakeRegistrar registrar;
  registrar.refresh_status = absl::PermissionDeniedError("read-only root");
  FakeClock clock(absl::FromUnixSeconds(2'000'000));
  ConsumerHeartbeat heartbeat(registrar, WindowsMetadata(false),
                              [&clock] { return clock.Now(); });

  EXPECT_EQ(heartbeat.RefreshIfDue().code(),
            absl::StatusCode::kPermissionDenied);
  EXPECT_FALSE(heartbeat.last_success().has_value());
  EXPECT_EQ(heartbeat.RefreshIfDue().code(),
            absl::StatusCode::kPermissionDenied);
  EXPECT_EQ(registrar.refreshes.size(), 2);

  registrar.refresh_status = absl::OkStatus();
  ASSERT_TRUE(heartbeat.RefreshIfDue().ok());
  ASSERT_TRUE(heartbeat.last_success().has_value());
  EXPECT_EQ(*heartbeat.last_success(), clock.Now());
}

TEST(ConsumerHeartbeatTest, ReprobedRuntimeCapabilityIsPublished) {
  FakeRegistrar registrar;
  FakeClock clock(absl::FromUnixSeconds(2'500'000));
  ConsumerHeartbeat heartbeat(registrar, WindowsMetadata(true),
                              [&clock] { return clock.Now(); });

  heartbeat.SetZenzaiV3ConditionsAvailable(false);
  ASSERT_TRUE(heartbeat.RefreshNow().ok());
  ASSERT_EQ(registrar.refreshes.size(), 1);
  EXPECT_FALSE(
      registrar.refreshes.back().capabilities.zenzai_v3_conditions);

  heartbeat.SetZenzaiV3ConditionsAvailable(true);
  ASSERT_TRUE(heartbeat.RefreshNow().ok());
  ASSERT_EQ(registrar.refreshes.size(), 2);
  EXPECT_TRUE(registrar.refreshes.back().capabilities.zenzai_v3_conditions);
}

TEST(ConsumerHeartbeatTest, ShutdownLeavesRecordAndUninstallRemovesOnlyOwnId) {
  FakeRegistrar registrar;
  FakeClock clock(absl::FromUnixSeconds(3'000'000));
  {
    ConsumerHeartbeat heartbeat(registrar, WindowsMetadata(),
                                [&clock] { return clock.Now(); });
    ASSERT_TRUE(heartbeat.RefreshNow().ok());
  }
  EXPECT_TRUE(registrar.unregisters.empty());

  ConsumerHeartbeat uninstall(registrar, WindowsMetadata(),
                              [&clock] { return clock.Now(); });
  ASSERT_TRUE(uninstall.Unregister().ok());
  ASSERT_EQ(registrar.unregisters.size(), 1);
  EXPECT_EQ(registrar.unregisters.front(), kTsfConsumerId);
}

TEST(DesktopConsumerHeartbeatBrandingTest,
     RequiresMozkeyBrandAndSupportedDesktopPlatform) {
  using desktop_consumer_heartbeat_internal::ShouldEnable;
  EXPECT_FALSE(ShouldEnable(/*is_mozkey_build=*/false,
                            /*is_supported_desktop_platform=*/false));
  EXPECT_FALSE(ShouldEnable(/*is_mozkey_build=*/false,
                            /*is_supported_desktop_platform=*/true));
  EXPECT_FALSE(ShouldEnable(/*is_mozkey_build=*/true,
                            /*is_supported_desktop_platform=*/false));
  EXPECT_TRUE(ShouldEnable(/*is_mozkey_build=*/true,
                           /*is_supported_desktop_platform=*/true));
}

}  // namespace
}  // namespace mozc::grimodex
