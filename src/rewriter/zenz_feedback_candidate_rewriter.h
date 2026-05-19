#ifndef MOZC_REWRITER_ZENZ_FEEDBACK_CANDIDATE_REWRITER_H_
#define MOZC_REWRITER_ZENZ_FEEDBACK_CANDIDATE_REWRITER_H_

#include "converter/segments.h"
#include "request/conversion_request.h"
#include "rewriter/rewriter_interface.h"

namespace mozc {

class ZenzFeedbackCandidateRewriter final : public RewriterInterface {
 public:
  ZenzFeedbackCandidateRewriter() = default;
  ZenzFeedbackCandidateRewriter(const ZenzFeedbackCandidateRewriter&) = delete;
  ZenzFeedbackCandidateRewriter& operator=(
      const ZenzFeedbackCandidateRewriter&) = delete;

  int capability(const ConversionRequest& request) const override;
  bool Rewrite(const ConversionRequest& request, Segments* segments) const override;
};

}  // namespace mozc

#endif  // MOZC_REWRITER_ZENZ_FEEDBACK_CANDIDATE_REWRITER_H_
