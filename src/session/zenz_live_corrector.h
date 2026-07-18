#ifndef MOZC_SESSION_ZENZ_LIVE_CORRECTOR_H_
#define MOZC_SESSION_ZENZ_LIVE_CORRECTOR_H_

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <thread>
#include <condition_variable>
#include <mutex>

#include "absl/time/time.h"

namespace mozc {
namespace session {

struct ZenzLiveRequest {
  uint32_t generation = 0;

  // Original Mozc reading, usually hiragana.
  std::string key;

  // zenz prompt:
  //   U+EE02 + left_context + U+EE00 + reading_katakana + U+EE01
  std::string prompt;

  std::string reading_katakana;
  std::string left_context;

  // Current visible Mozc live conversion result.
  std::string mozc_value;

  // Runtime options.
  std::string pipe_name;
  // Empty means automatic (GPU preferred); "none" forces CPU-only inference.
  // Other values are names returned by llama-server --list-devices.
  std::string backend_device;
  uint32_t timeout_msec = 180;
  uint32_t max_output_chars = 128;

  absl::Time issued_at;
};

struct ZenzLiveResponse {
  uint32_t generation = 0;
  std::string key;
  std::string value;
  std::string debug;

  bool ok = false;
  bool timeout = false;

  absl::Duration latency = absl::ZeroDuration();
};

class ZenzClient {
 public:
  virtual ~ZenzClient() = default;

  virtual bool IsAvailable() const = 0;

  virtual ZenzLiveResponse Convert(const ZenzLiveRequest& request) = 0;
};

class ZenzLiveCorrector {
 public:
  explicit ZenzLiveCorrector(std::unique_ptr<ZenzClient> client);
  ~ZenzLiveCorrector();

  ZenzLiveCorrector(const ZenzLiveCorrector&) = delete;
  ZenzLiveCorrector& operator=(const ZenzLiveCorrector&) = delete;

  void Start();
  void Stop();

  // Latest-only submission. A queued old request is overwritten.
  void Submit(ZenzLiveRequest request);

  // Clears queued request/result. Running inference is not forcibly cancelled;
  // stale discard is handled by generation check.
  void CancelPending();

  // Returns the latest result only if its generation matches.
  std::optional<ZenzLiveResponse> TakeResult(uint32_t generation);

 private:
  void WorkerLoop();

  std::unique_ptr<ZenzClient> client_;

  std::mutex mu_;
  std::condition_variable cv_;

  bool started_ = false;
  bool stop_ = false;

  std::optional<ZenzLiveRequest> latest_request_;
  std::optional<ZenzLiveResponse> latest_result_;

  std::thread worker_;
};

}  // namespace session
}  // namespace mozc

#endif  // MOZC_SESSION_ZENZ_LIVE_CORRECTOR_H_
