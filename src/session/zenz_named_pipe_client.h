#ifndef MOZC_SESSION_ZENZ_NAMED_PIPE_CLIENT_H_
#define MOZC_SESSION_ZENZ_NAMED_PIPE_CLIENT_H_

#include "session/zenz_live_corrector.h"

namespace mozc {
namespace session {

class ZenzNamedPipeClient final : public ZenzClient {
 public:
  ZenzNamedPipeClient() = default;
  ~ZenzNamedPipeClient() override = default;

  bool IsAvailable() const override;
  ZenzLiveResponse Convert(const ZenzLiveRequest& request) override;
};

}  // namespace session
}  // namespace mozc

#endif  // MOZC_SESSION_ZENZ_NAMED_PIPE_CLIENT_H_
