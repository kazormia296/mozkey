// Copyright 2010-2021, Google Inc.
// All rights reserved.
//
// Redistribution and use in source and binary forms, with or without
// modification, are permitted provided that the following conditions are
// met:
//
//     * Redistributions of source code must retain the above copyright
// notice, this list of conditions and the following disclaimer.
//     * Redistributions in binary form must reproduce the above
// copyright notice, this list of conditions and the following disclaimer
// in the documentation and/or other materials provided with the
// distribution.
//     * Neither the name of Google Inc. nor the names of its
// contributors may be used to endorse or promote products derived from
// this software without specific prior written permission.
//
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
// "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
// LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
// A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
// OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
// SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
// LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
// DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
// THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
// (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
// OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

#import "renderer/mac/CandidateView.h"

#include <cstdint>

#include "client/client_interface.h"
#include "protocol/candidate_window.pb.h"
#include "protocol/commands.pb.h"
#include "testing/gunit.h"

@interface CandidateView (CallbackProvenanceTesting)
- (BOOL)sendCandidateCommandForRow:(int)row
                    mouseDownToken:(uint64_t)mouseDownToken;
@end

namespace mozc::renderer::mac {
namespace {

class CapturingCommandSender final : public client::SendCommandInterface {
 public:
  bool SendCommand(const commands::SessionCommand &command,
                   commands::Output *output) override {
    (void)output;
    ++call_count_;
    command_ = command;
    return true;
  }

  int call_count() const { return call_count_; }
  const commands::SessionCommand &command() const { return command_; }

 private:
  int call_count_ = 0;
  commands::SessionCommand command_;
};

TEST(CandidateViewCallbackProvenanceTest, EchoesDisplayedSnapshotToken) {
  CandidateView *view =
      [[CandidateView alloc] initWithFrame:NSMakeRect(0, 0, 1, 1)];
  CapturingCommandSender sender;
  [view setSendCommandInterface:&sender];
  [view setRendererCallbackToken:41];

  commands::CandidateWindow candidates;
  candidates.set_size(1);
  candidates.set_position(0);
  commands::CandidateWindow::Candidate *candidate = candidates.add_candidate();
  candidate->set_index(0);
  candidate->set_value("candidate");
  candidate->set_id(7);
  [view setCandidateWindow:&candidates];

  EXPECT_TRUE([view sendCandidateCommandForRow:0 mouseDownToken:41]);
  ASSERT_EQ(sender.call_count(), 1);
  EXPECT_EQ(sender.command().type(),
            commands::SessionCommand::SELECT_CANDIDATE);
  EXPECT_EQ(sender.command().id(), 7);
  EXPECT_EQ(sender.command().renderer_callback_token(), 41);
}

TEST(CandidateViewCallbackProvenanceTest, RejectsGestureSpanningRendererUpdate) {
  CandidateView *view =
      [[CandidateView alloc] initWithFrame:NSMakeRect(0, 0, 1, 1)];
  CapturingCommandSender sender;
  [view setSendCommandInterface:&sender];

  commands::CandidateWindow candidates;
  candidates.set_size(1);
  candidates.set_position(0);
  commands::CandidateWindow::Candidate *candidate = candidates.add_candidate();
  candidate->set_index(0);
  candidate->set_value("candidate");
  candidate->set_id(7);
  [view setCandidateWindow:&candidates];
  [view setRendererCallbackToken:41];

  // The gesture began against token 41, but the view was replaced before its
  // mouse-up event reached the renderer loop.
  [view setRendererCallbackToken:42];
  EXPECT_FALSE([view sendCandidateCommandForRow:0 mouseDownToken:41]);
  EXPECT_EQ(sender.call_count(), 0);
}

TEST(CandidateViewCallbackProvenanceTest, RejectsUnscopedOrInvalidCallbacks) {
  CandidateView *view =
      [[CandidateView alloc] initWithFrame:NSMakeRect(0, 0, 1, 1)];
  CapturingCommandSender sender;
  [view setSendCommandInterface:&sender];
  commands::CandidateWindow candidates;
  candidates.set_size(1);
  candidates.set_position(0);
  commands::CandidateWindow::Candidate *candidate = candidates.add_candidate();
  candidate->set_index(0);
  candidate->set_value("candidate");
  candidate->set_id(7);
  [view setCandidateWindow:&candidates];

  [view setRendererCallbackToken:0];
  EXPECT_FALSE([view sendCandidateCommandForRow:0 mouseDownToken:0]);
  [view setRendererCallbackToken:9];
  EXPECT_FALSE([view sendCandidateCommandForRow:1 mouseDownToken:9]);
  EXPECT_EQ(sender.call_count(), 0);
}

}  // namespace
}  // namespace mozc::renderer::mac
