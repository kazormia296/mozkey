// Copyright 2026 The Mozkey Authors

#include "grimodex/consumer_handshake.h"

#include <array>
#include <string>

#include "absl/status/status.h"
#include "absl/status/statusor.h"
#include "absl/strings/string_view.h"
#include "absl/time/time.h"
#include "google/protobuf/struct.pb.h"
#include "google/protobuf/util/json_util.h"
#include "testing/gunit.h"

namespace mozc::grimodex {
namespace {

constexpr absl::string_view kTimestamp = "2026-07-18T01:02:03.456Z";

ConsumerHandshake MakeHandshake(absl::string_view consumer_id,
                                absl::string_view name,
                                absl::string_view platform) {
  return ConsumerHandshake{
      .consumer_id = std::string(consumer_id),
      .name = std::string(name),
      .version = "v0.7.7",
      .platform = std::string(platform),
      .last_seen = std::string(kTimestamp),
      .capabilities =
          ConsumerCapabilities{
              .profile = true,
              .dynamic_dictionary = true,
              .zenzai_v3_conditions = false,
              .application_scoping = true,
          },
  };
}

google::protobuf::Struct ParseJson(absl::string_view bytes) {
  google::protobuf::Struct result;
  EXPECT_TRUE(
      google::protobuf::util::JsonStringToMessage(bytes, &result).ok());
  return result;
}

std::string StringField(const google::protobuf::Struct &value,
                        absl::string_view field) {
  const auto iterator = value.fields().find(std::string(field));
  EXPECT_NE(iterator, value.fields().end()) << field;
  return iterator == value.fields().end() ? std::string()
                                          : iterator->second.string_value();
}

bool ZenzCapability(const google::protobuf::Struct &value) {
  return value.fields()
      .at("capabilities")
      .struct_value()
      .fields()
      .at("zenzai_v3_conditions")
      .bool_value();
}

TEST(ConsumerHandshakeTest, SerializesAllCanonicalPlatformConsumers) {
  struct PlatformConsumer {
    absl::string_view consumer_id;
    absl::string_view name;
    absl::string_view platform;
  };
  constexpr std::array<PlatformConsumer, 3> kConsumers = {{
      {kFcitx5ConsumerId, "Mozkey IbG for Grimodex on Linux", "linux"},
      {kTsfConsumerId, "Mozkey IbG for Grimodex on Windows", "windows"},
      {kImkitConsumerId, "Mozkey IbG for Grimodex on macOS", "macos"},
  }};

  for (const PlatformConsumer &consumer : kConsumers) {
    const ConsumerHandshake handshake =
        MakeHandshake(consumer.consumer_id, consumer.name, consumer.platform);
    absl::StatusOr<std::string> payload =
        SerializeConsumerHandshake(handshake);
    ASSERT_TRUE(payload.ok()) << payload.status();
    EXPECT_EQ(payload->back(), '\n');
    EXPECT_LE(payload->size(), kMaxConsumerHandshakeBytes);

    const google::protobuf::Struct parsed = ParseJson(*payload);
    EXPECT_EQ(parsed.fields().at("format_version").number_value(), 1);
    EXPECT_EQ(StringField(parsed, "consumer_id"), consumer.consumer_id);
    EXPECT_EQ(StringField(parsed, "name"), consumer.name);
    EXPECT_EQ(StringField(parsed, "version"), "v0.7.7");
    EXPECT_EQ(StringField(parsed, "platform"), consumer.platform);
    EXPECT_EQ(StringField(parsed, "last_seen"), kTimestamp);
  }
}

TEST(ConsumerHandshakeTest, UsesDeterministicCanonicalFieldOrder) {
  const ConsumerHandshake handshake = MakeHandshake(
      kFcitx5ConsumerId, "Mozkey IbG for Grimodex on Linux", "linux");
  absl::StatusOr<std::string> payload = SerializeConsumerHandshake(handshake);
  ASSERT_TRUE(payload.ok()) << payload.status();
  EXPECT_EQ(
      *payload,
      R"json({"capabilities":{"application_scoping":true,"dynamic_dictionary":true,"profile":true,"zenzai_v3_conditions":false},"consumer_id":"fcitx5-mozkey-ibg","format_version":1,"last_seen":"2026-07-18T01:02:03.456Z","name":"Mozkey IbG for Grimodex on Linux","platform":"linux","version":"v0.7.7"})json"
      "\n");
}

TEST(ConsumerHandshakeTest, EscapesJsonMetadataWithoutChangingItsValue) {
  ConsumerHandshake handshake =
      MakeHandshake(kTsfConsumerId, "unused", "windows");
  handshake.name = "Mozkey \\\"quoted\\\" \\\\\\ path\nline\t";
  handshake.name.push_back('\x01');

  absl::StatusOr<std::string> payload = SerializeConsumerHandshake(handshake);
  ASSERT_TRUE(payload.ok()) << payload.status();
  EXPECT_NE(payload->find(R"json(Mozkey \\\"quoted\\\" \\\\\\ path\nline\t\u0001)json"),
            std::string::npos);
  EXPECT_EQ(StringField(ParseJson(*payload), "name"), handshake.name);
}

TEST(ConsumerHandshakeTest, RejectsUnsafeOrUnboundedMetadata) {
  ConsumerHandshake handshake =
      MakeHandshake(kTsfConsumerId, "Mozkey for Grimodex", "windows");

  handshake.consumer_id = "../tsf-mozkey-ibg";
  EXPECT_EQ(SerializeConsumerHandshake(handshake).status().code(),
            absl::StatusCode::kInvalidArgument);
  handshake.consumer_id = ".tsf-mozkey-ibg";
  EXPECT_EQ(SerializeConsumerHandshake(handshake).status().code(),
            absl::StatusCode::kInvalidArgument);
  handshake.consumer_id = std::string(kTsfConsumerId);

  handshake.version = "v1\"injected";
  EXPECT_EQ(SerializeConsumerHandshake(handshake).status().code(),
            absl::StatusCode::kInvalidArgument);
  handshake.version = "v0.7.7";

  handshake.platform = "windows/../linux";
  EXPECT_EQ(SerializeConsumerHandshake(handshake).status().code(),
            absl::StatusCode::kInvalidArgument);
  handshake.platform = "windows";

  handshake.last_seen = "2026-07-18T01:02:03+00:00";
  EXPECT_EQ(SerializeConsumerHandshake(handshake).status().code(),
            absl::StatusCode::kInvalidArgument);
  handshake.last_seen = std::string(kTimestamp);

  handshake.name.assign(257, 'x');
  EXPECT_EQ(SerializeConsumerHandshake(handshake).status().code(),
            absl::StatusCode::kInvalidArgument);
  handshake.name.assign("\xc0\xaf", 2);
  EXPECT_EQ(SerializeConsumerHandshake(handshake).status().code(),
            absl::StatusCode::kInvalidArgument);
}

TEST(ConsumerHandshakeTest, ReportsTheTruthfulZenzCapability) {
  ConsumerHandshake handshake =
      MakeHandshake(kImkitConsumerId, "Mozkey IbG for Grimodex on macOS", "macos");
  handshake.capabilities.zenzai_v3_conditions = false;
  absl::StatusOr<std::string> unavailable =
      SerializeConsumerHandshake(handshake);
  ASSERT_TRUE(unavailable.ok()) << unavailable.status();
  EXPECT_FALSE(ZenzCapability(ParseJson(*unavailable)));

  handshake.capabilities.zenzai_v3_conditions = true;
  absl::StatusOr<std::string> available = SerializeConsumerHandshake(handshake);
  ASSERT_TRUE(available.ok()) << available.status();
  EXPECT_TRUE(ZenzCapability(ParseJson(*available)));
}

TEST(ConsumerHandshakeTest, RefreshesAtBoundaryAndAfterClockRollback) {
  EXPECT_EQ(kConsumerRefreshInterval, absl::Seconds(900));
  EXPECT_EQ(kConsumerExpiry, absl::Seconds(2700));
  const absl::Time last_success = absl::FromUnixSeconds(1'000'000);

  EXPECT_FALSE(ShouldRefresh(last_success, last_success));
  EXPECT_FALSE(
      ShouldRefresh(last_success, last_success + absl::Seconds(899)));
  EXPECT_TRUE(ShouldRefresh(last_success,
                            last_success + kConsumerRefreshInterval));
  EXPECT_TRUE(ShouldRefresh(last_success, last_success - absl::Seconds(1)));
}

}  // namespace
}  // namespace mozc::grimodex
