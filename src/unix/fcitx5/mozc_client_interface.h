#ifndef UNIX_FCITX5_MOZC_CLIENT_INTERFACE_H_
#define UNIX_FCITX5_MOZC_CLIENT_INTERFACE_H_

#include <cstdint>
#include <memory>
#include <string>
#include <string_view>

#include "protocol/commands.pb.h"
#include "protocol/config.pb.h"

namespace fcitx {

// This is a simplified version of mozc::ClientInterface, with only functions
// Needed by Fcitx.
class MozcClientInterface {
 public:
  virtual ~MozcClientInterface() = default;
  virtual bool EnsureConnection() = 0;
  virtual bool EnsureSession() = 0;
  // Changes only when the concrete client creates a replacement Mozc
  // session.  Candidate IDs and callbacks are valid only within one value.
  virtual uint64_t session_generation() const = 0;
  virtual bool SendKeyWithContext(const mozc::commands::KeyEvent& key,
                                  const mozc::commands::Context& context,
                                  mozc::commands::Output* output) = 0;
  virtual bool SendCommandWithContext(
      const mozc::commands::SessionCommand& command,
      const mozc::commands::Context& context,
      mozc::commands::Output* output) = 0;
  virtual bool IsDirectModeCommand(
      const mozc::commands::KeyEvent& key) const = 0;
  virtual bool GetConfig(mozc::config::Config* config) = 0;
  virtual void set_client_capability(
      const mozc::commands::Capability& capability) = 0;
  virtual bool SyncData() = 0;
  virtual bool LaunchTool(const std::string& mode, std::string_view arg) = 0;
  virtual bool LaunchToolWithProtoBuf(const mozc::commands::Output& output) = 0;
};

std::unique_ptr<MozcClientInterface> createClient();

}  // namespace fcitx
#endif
