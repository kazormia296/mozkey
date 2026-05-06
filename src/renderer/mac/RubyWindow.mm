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

#include "renderer/mac/RubyWindow.h"

#import <Cocoa/Cocoa.h>

#include <string>

#include "protocol/commands.pb.h"
#include "protocol/renderer_command.pb.h"

namespace {

constexpr CGFloat kPaddingX = 14.0;
constexpr CGFloat kPaddingY = 6.0;
constexpr CGFloat kCornerRadius = 5.0;
constexpr CGFloat kFontSize = 13.0;

NSString *ToNSString(const std::string &text) {
  return [[NSString alloc] initWithUTF8String:text.c_str()];
}

}  // namespace

@interface RubyView : NSView
- (void)setRubyText:(NSString *)text;
- (NSSize)preferredSize;
@end

@implementation RubyView {
  NSString *text_;
  NSDictionary<NSAttributedStringKey, id> *attributes_;
}

- (instancetype)initWithFrame:(NSRect)frame {
  self = [super initWithFrame:frame];
  if (self) {
    text_ = @"";
    NSFont *font = [NSFont fontWithName:@"Hiragino Sans" size:kFontSize];
    if (font == nil) {
      font = [NSFont systemFontOfSize:kFontSize weight:NSFontWeightSemibold];
    }
    attributes_ = @{
      NSFontAttributeName : font,
      NSForegroundColorAttributeName : NSColor.whiteColor,
    };
    [self setWantsLayer:YES];
  }
  return self;
}

- (void)setRubyText:(NSString *)text {
  text_ = [text copy];
  [self setNeedsDisplay:YES];
}

- (NSSize)preferredSize {
  NSSize textSize = [text_ sizeWithAttributes:attributes_];
  return NSMakeSize(ceil(textSize.width + kPaddingX * 2.0),
                    ceil(textSize.height + kPaddingY * 2.0));
}

- (void)drawRect:(NSRect)dirtyRect {
  [super drawRect:dirtyRect];

  [[NSColor colorWithCalibratedWhite:0.10 alpha:0.90] setFill];
  NSBezierPath *background =
      [NSBezierPath bezierPathWithRoundedRect:self.bounds
                                     xRadius:kCornerRadius
                                     yRadius:kCornerRadius];
  [background fill];

  NSSize textSize = [text_ sizeWithAttributes:attributes_];
  NSRect textRect = NSMakeRect((NSWidth(self.bounds) - textSize.width) / 2.0,
                               (NSHeight(self.bounds) - textSize.height) / 2.0,
                               textSize.width, textSize.height);
  [text_ drawInRect:textRect withAttributes:attributes_];
}
@end

namespace mozc {
namespace renderer {
namespace mac {

RubyWindow::RubyWindow() = default;

RubyWindow::~RubyWindow() = default;

bool RubyWindow::BuildReadingText(const commands::RendererCommand &command,
                                  std::string *reading) const {
  reading->clear();

  if (!command.has_output()) {
    return false;
  }

  const commands::Output &output = command.output();
  if (!output.live_conversion() || !output.has_preedit()) {
    return false;
  }

  const commands::Preedit &preedit = output.preedit();
  for (int i = 0; i < preedit.segment_size(); ++i) {
    const commands::Preedit::Segment &segment = preedit.segment(i);
    if (segment.has_key() && !segment.key().empty()) {
      reading->append(segment.key());
    } else {
      reading->append(segment.value());
    }
  }

  return !reading->empty();
}

bool RubyWindow::Update(const commands::RendererCommand &command) {
  std::string reading;
  if (!BuildReadingText(command, &reading)) {
    return false;
  }

  if (!window_) {
    InitWindow();
  }

  RubyView *ruby_view = (RubyView *)view_;
  [ruby_view setRubyText:ToNSString(reading)];
  const NSSize size = [ruby_view preferredSize];
  ResizeWindow(size.width, size.height);
  return true;
}

void RubyWindow::InitWindow() {
  RendererBaseWindow::InitWindow();
  [window_ setOpaque:NO];
  [window_ setHasShadow:YES];
  [window_ setBackgroundColor:NSColor.clearColor];
  [window_ setIgnoresMouseEvents:YES];
}

void RubyWindow::ResetView() {
  view_ = [[RubyView alloc] initWithFrame:NSMakeRect(0, 0, 1, 1)];
}

}  // namespace mac
}  // namespace renderer
}  // namespace mozc