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

#import "mac/renderer_receiver.h"

#include <cstdint>

#include "protocol/commands.pb.h"

@implementation RendererReceiver {
  /** The current active controller that handles events from the renderer process. */
  id<ControllerCallback> _currentController;

  /** NSConnection to communicate with the renderer process. */
  NSConnection *_rendererConnection;

  /** Revocable token for callbacks captured for the current controller. */
  uint64_t _controllerGeneration;
}

- (id)initWithName:(NSString *)name {
  self = [super init];
  if (self) {
    // _rendererConnection receives IPC calls from the renderer process.
    // See: renderer/mac/mac_server_send_command.mm
    _rendererConnection = [[NSConnection alloc] init];
    [_rendererConnection setRootObject:self];
    [_rendererConnection registerName:name];
  }
  return self;
}

#pragma mark ServerCallback
// Methods inherited from the ServerCallback protocol (see: common.h).

- (uint64_t)currentControllerGeneration {
  @synchronized(self) {
    return _controllerGeneration;
  }
}

- (void)dispatchCommand:(const mozc::commands::SessionCommand &)command
            controller:(id<ControllerCallback>)controller
     expectedGeneration:(uint64_t)expectedGeneration {
  // Keep registration changes serialized with the callback.  This closes the
  // window between validating the generation and invoking a controller that
  // has already been deactivated or superseded.
  @synchronized(self) {
    if (expectedGeneration == 0 || expectedGeneration != _controllerGeneration ||
        controller == nil || controller != _currentController) {
      return;
    }
    [controller sendCommand:command];
  }
}

- (void)dispatchOutput:(const mozc::commands::Output &)output
           controller:(id<ControllerCallback>)controller
    expectedGeneration:(uint64_t)expectedGeneration {
  @synchronized(self) {
    if (expectedGeneration == 0 || expectedGeneration != _controllerGeneration ||
        controller == nil || controller != _currentController) {
      return;
    }
    [controller outputResult:output];
  }
}

// sendData is a method of the ServerCallback protocol.
- (void)sendData:(NSData *)data {
  id<ControllerCallback> controller = nil;
  uint64_t generation = 0;
  @synchronized(self) {
    controller = _currentController;
    generation = _controllerGeneration;
  }
  if (controller == nil || generation == 0) {
    return;
  }

  mozc::commands::SessionCommand command;
  const int32_t length = static_cast<int32_t>([data length]);
  if (!command.ParseFromArray([data bytes], length)) {
    return;
  }
  [self dispatchCommand:command
             controller:controller
      expectedGeneration:generation];
}

// outputResult is a method of the ServerCallback protocol.
- (void)outputResult:(NSData *)result {
  id<ControllerCallback> controller = nil;
  uint64_t generation = 0;
  @synchronized(self) {
    controller = _currentController;
    generation = _controllerGeneration;
  }
  if (controller == nil || generation == 0) {
    return;
  }

  mozc::commands::Output output;
  const int32_t length = static_cast<int32_t>([result length]);
  if (!output.ParseFromArray([result bytes], length)) {
    return;
  }

  [self dispatchOutput:output
            controller:controller
     expectedGeneration:generation];
}

// setCurrentController is a method of the ServerCallback protocol.
- (void)setCurrentController:(id<ControllerCallback>)controller {
  @synchronized(self) {
    _currentController = controller;
    ++_controllerGeneration;
    if (_controllerGeneration == 0) {
      ++_controllerGeneration;
    }
  }
}

- (void)clearCurrentController:(id<ControllerCallback>)controller {
  @synchronized(self) {
    if (controller == nil || controller != _currentController) {
      return;
    }
    _currentController = nil;
    ++_controllerGeneration;
    if (_controllerGeneration == 0) {
      ++_controllerGeneration;
    }
  }
}
@end
