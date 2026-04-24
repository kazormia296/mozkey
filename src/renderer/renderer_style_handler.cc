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

#include "renderer/renderer_style_handler.h"

#include "base/singleton.h"
#include "protocol/renderer_style.pb.h"

namespace mozc {
namespace renderer {
namespace {

void SetColor(RendererStyle::RGBAColor* color, int r, int g, int b) {
  color->set_r(r);
  color->set_g(g);
  color->set_b(b);
}

RendererStyle::TextStyle* GetTextStyle(RendererStyle* style, int index) {
  while (style->text_styles_size() <= index) {
    style->add_text_styles();
  }
  return style->mutable_text_styles(index);
}

void ApplyLightCandidateWindowTheme(RendererStyle* style) {
  SetColor(style->mutable_border_color(), 0x96, 0x96, 0x96);

  RendererStyle::TextStyle* shortcut_style = GetTextStyle(style, 0);
  SetColor(shortcut_style->mutable_foreground_color(), 0x77, 0x77, 0x77);
  SetColor(shortcut_style->mutable_background_color(), 0xf3, 0xf4, 0xff);

  RendererStyle::TextStyle* candidate_style = GetTextStyle(style, 2);
  SetColor(candidate_style->mutable_foreground_color(), 0x00, 0x00, 0x00);
  SetColor(candidate_style->mutable_background_color(), 0xff, 0xff, 0xff);

  RendererStyle::TextStyle* description_style = GetTextStyle(style, 3);
  SetColor(description_style->mutable_foreground_color(), 0x88, 0x88, 0x88);
  SetColor(description_style->mutable_background_color(), 0xff, 0xff, 0xff);

  SetColor(style->mutable_footer_style()->mutable_foreground_color(),
           0x4c, 0x4c, 0x4c);
  SetColor(style->mutable_footer_sub_label_style()->mutable_foreground_color(),
           0xa7, 0xa7, 0xa7);

  if (style->footer_border_colors_size() == 0) {
    style->add_footer_border_colors();
  }
  SetColor(style->mutable_footer_border_colors(0), 0x60, 0x60, 0x60);

  SetColor(style->mutable_footer_top_color(), 0xff, 0xff, 0xff);
  SetColor(style->mutable_footer_bottom_color(), 0xee, 0xee, 0xee);

  SetColor(style->mutable_focused_background_color(), 0xd1, 0xea, 0xff);
  SetColor(style->mutable_focused_border_color(), 0x7f, 0xac, 0xdd);

  SetColor(style->mutable_scrollbar_background_color(), 0xe0, 0xe0, 0xe0);
  SetColor(style->mutable_scrollbar_indicator_color(), 0x75, 0x90, 0xb8);

  RendererStyle::InfolistStyle* infostyle = style->mutable_infolist_style();
  SetColor(infostyle->mutable_caption_style()->mutable_foreground_color(),
           0x00, 0x00, 0x00);
  SetColor(infostyle->mutable_title_style()->mutable_foreground_color(),
           0x00, 0x00, 0x00);
  SetColor(infostyle->mutable_title_style()->mutable_background_color(),
           0xff, 0xff, 0xff);
  SetColor(infostyle->mutable_description_style()->mutable_foreground_color(),
           0x33, 0x33, 0x33);
  SetColor(infostyle->mutable_description_style()->mutable_background_color(),
           0xff, 0xff, 0xff);
  SetColor(infostyle->mutable_border_color(), 0x96, 0x96, 0x96);
  SetColor(infostyle->mutable_caption_background_color(), 0xec, 0xf0, 0xfa);
  SetColor(infostyle->mutable_focused_background_color(), 0xd1, 0xea, 0xff);
  SetColor(infostyle->mutable_focused_border_color(), 0x7f, 0xac, 0xdd);
}

void ApplyDarkCandidateWindowTheme(RendererStyle* style) {
  SetColor(style->mutable_border_color(), 0x32, 0x38, 0x40);

  RendererStyle::TextStyle* shortcut_style = GetTextStyle(style, 0);
  SetColor(shortcut_style->mutable_foreground_color(), 0x96, 0xa0, 0xaa);
  SetColor(shortcut_style->mutable_background_color(), 0x18, 0x1b, 0x20);

  RendererStyle::TextStyle* candidate_style = GetTextStyle(style, 2);
  SetColor(candidate_style->mutable_foreground_color(), 0xe6, 0xed, 0xf3);
  SetColor(candidate_style->mutable_background_color(), 0x18, 0x1b, 0x20);

  RendererStyle::TextStyle* description_style = GetTextStyle(style, 3);
  SetColor(description_style->mutable_foreground_color(), 0x8b, 0x94, 0x9e);
  SetColor(description_style->mutable_background_color(), 0x18, 0x1b, 0x20);

  SetColor(style->mutable_footer_style()->mutable_foreground_color(),
           0xb7, 0xc0, 0xc9);
  SetColor(style->mutable_footer_sub_label_style()->mutable_foreground_color(),
           0x77, 0x80, 0x8a);

  if (style->footer_border_colors_size() == 0) {
    style->add_footer_border_colors();
  }
  SetColor(style->mutable_footer_border_colors(0), 0x2a, 0x30, 0x37);

  SetColor(style->mutable_footer_top_color(), 0x16, 0x1a, 0x1f);
  SetColor(style->mutable_footer_bottom_color(), 0x16, 0x1a, 0x1f);

  SetColor(style->mutable_focused_background_color(), 0x24, 0x2b, 0x34);
  SetColor(style->mutable_focused_border_color(), 0x3f, 0x4b, 0x59);

  SetColor(style->mutable_scrollbar_background_color(), 0x1d, 0x22, 0x28);
  SetColor(style->mutable_scrollbar_indicator_color(), 0x4b, 0x57, 0x66);

  RendererStyle::InfolistStyle* infostyle = style->mutable_infolist_style();
  SetColor(infostyle->mutable_caption_style()->mutable_foreground_color(),
           0xe6, 0xed, 0xf3);
  SetColor(infostyle->mutable_title_style()->mutable_foreground_color(),
           0xe6, 0xed, 0xf3);
  SetColor(infostyle->mutable_title_style()->mutable_background_color(),
           0x18, 0x1b, 0x20);
  SetColor(infostyle->mutable_description_style()->mutable_foreground_color(),
           0xc9, 0xd1, 0xd9);
  SetColor(infostyle->mutable_description_style()->mutable_background_color(),
           0x18, 0x1b, 0x20);
  SetColor(infostyle->mutable_border_color(), 0x32, 0x38, 0x40);
  SetColor(infostyle->mutable_caption_background_color(), 0x1a, 0x1e, 0x24);
  SetColor(infostyle->mutable_focused_background_color(), 0x24, 0x2b, 0x34);
  SetColor(infostyle->mutable_focused_border_color(), 0x3f, 0x4b, 0x59);
}

class RendererStyleHandlerImpl {
 public:
  RendererStyleHandlerImpl();
  ~RendererStyleHandlerImpl() = default;
  bool GetRendererStyle(RendererStyle* style);
  bool SetRendererStyle(const RendererStyle& style);
  void GetDefaultRendererStyle(RendererStyle* style);

 private:
  RendererStyle style_;
};

RendererStyleHandlerImpl* GetRendererStyleHandlerImpl() {
  return Singleton<RendererStyleHandlerImpl>::get();
}

RendererStyleHandlerImpl::RendererStyleHandlerImpl() {
  RendererStyle style;
  GetDefaultRendererStyle(&style);
  SetRendererStyle(style);
}

bool RendererStyleHandlerImpl::GetRendererStyle(RendererStyle* style) {
  *style = this->style_;
  return true;
}
bool RendererStyleHandlerImpl::SetRendererStyle(const RendererStyle& style) {
  style_ = style;
  return true;
}
void RendererStyleHandlerImpl::GetDefaultRendererStyle(RendererStyle* style) {
  // TODO(horo): Change to read from human-readable ASCII format protobuf.
  style->Clear();
  style->set_window_border(1);
  style->set_scrollbar_width(4);
  style->set_row_rect_padding(0);
  style->mutable_border_color()->set_r(0x96);
  style->mutable_border_color()->set_g(0x96);
  style->mutable_border_color()->set_b(0x96);

  RendererStyle::TextStyle* shortcutStyle = style->add_text_styles();
  shortcutStyle->set_font_size(12);
  shortcutStyle->mutable_foreground_color()->set_r(0x77);
  shortcutStyle->mutable_foreground_color()->set_g(0x77);
  shortcutStyle->mutable_foreground_color()->set_b(0x77);
  shortcutStyle->mutable_background_color()->set_r(0xf3);
  shortcutStyle->mutable_background_color()->set_g(0xf4);
  shortcutStyle->mutable_background_color()->set_b(0xff);
  shortcutStyle->set_left_padding(6);
  shortcutStyle->set_right_padding(6);

  RendererStyle::TextStyle* gap1Style = style->add_text_styles();
  gap1Style->set_font_size(12);

  RendererStyle::TextStyle* candidateStyle = style->add_text_styles();
  candidateStyle->set_font_size(15);

  RendererStyle::TextStyle* descriptionStyle = style->add_text_styles();
  descriptionStyle->set_font_size(11);
  descriptionStyle->mutable_foreground_color()->set_r(0x88);
  descriptionStyle->mutable_foreground_color()->set_g(0x88);
  descriptionStyle->mutable_foreground_color()->set_b(0x88);
  descriptionStyle->set_right_padding(10);

  // We want to ensure that the candidate window is at least wide
  // enough to render "そのほかの文字種  " as a candidate.
  style->set_column_minimum_width_string("そのほかの文字種  ");

  style->mutable_footer_style()->set_font_size(12);
  style->mutable_footer_style()->set_left_padding(6);
  style->mutable_footer_style()->set_right_padding(6);

  RendererStyle::TextStyle* footer_sub_label_style =
      style->mutable_footer_sub_label_style();
  footer_sub_label_style->set_font_size(9);
  footer_sub_label_style->mutable_foreground_color()->set_r(167);
  footer_sub_label_style->mutable_foreground_color()->set_g(167);
  footer_sub_label_style->mutable_foreground_color()->set_b(167);
  footer_sub_label_style->set_left_padding(6);
  footer_sub_label_style->set_right_padding(6);

  RendererStyle::RGBAColor* color = style->add_footer_border_colors();
  color->set_r(96);
  color->set_g(96);
  color->set_b(96);

  style->mutable_footer_top_color()->set_r(0xff);
  style->mutable_footer_top_color()->set_g(0xff);
  style->mutable_footer_top_color()->set_b(0xff);

  style->mutable_footer_bottom_color()->set_r(0xee);
  style->mutable_footer_bottom_color()->set_g(0xee);
  style->mutable_footer_bottom_color()->set_b(0xee);

  style->set_logo_file_name("candidate_window_logo.tiff");

  style->mutable_focused_background_color()->set_r(0xd1);
  style->mutable_focused_background_color()->set_g(0xea);
  style->mutable_focused_background_color()->set_b(0xff);

  style->mutable_focused_border_color()->set_r(0x7f);
  style->mutable_focused_border_color()->set_g(0xac);
  style->mutable_focused_border_color()->set_b(0xdd);

  style->mutable_scrollbar_background_color()->set_r(0xe0);
  style->mutable_scrollbar_background_color()->set_g(0xe0);
  style->mutable_scrollbar_background_color()->set_b(0xe0);

  style->mutable_scrollbar_indicator_color()->set_r(0x75);
  style->mutable_scrollbar_indicator_color()->set_g(0x90);
  style->mutable_scrollbar_indicator_color()->set_b(0xb8);

  RendererStyle::InfolistStyle* infostyle = style->mutable_infolist_style();
  infostyle->set_caption_string("用例");
  infostyle->set_caption_height(20);
  infostyle->set_caption_padding(1);
  infostyle->mutable_caption_style()->set_font_size(12);
  infostyle->mutable_caption_style()->set_left_padding(2);
  infostyle->mutable_caption_background_color()->set_r(0xec);
  infostyle->mutable_caption_background_color()->set_g(0xf0);
  infostyle->mutable_caption_background_color()->set_b(0xfa);

  infostyle->set_window_border(1);
  infostyle->set_row_rect_padding(2);
  infostyle->set_window_width(300);
  infostyle->mutable_title_style()->set_font_size(15);
  infostyle->mutable_title_style()->set_left_padding(5);
  infostyle->mutable_description_style()->set_font_size(12);
  infostyle->mutable_description_style()->set_left_padding(15);

  ApplyLightCandidateWindowTheme(style);
}

}  // namespace

bool RendererStyleHandler::GetRendererStyle(RendererStyle* style) {
  return GetRendererStyleHandlerImpl()->GetRendererStyle(style);
}
bool RendererStyleHandler::SetRendererStyle(const RendererStyle& style) {
  return GetRendererStyleHandlerImpl()->SetRendererStyle(style);
}
void RendererStyleHandler::GetDefaultRendererStyle(RendererStyle* style) {
  return GetRendererStyleHandlerImpl()->GetDefaultRendererStyle(style);
}

void RendererStyleHandler::ApplyCandidateWindowTheme(bool use_dark_mode,
                                                     RendererStyle* style) {
  if (use_dark_mode) {
    ApplyDarkCandidateWindowTheme(style);
  } else {
    ApplyLightCandidateWindowTheme(style);
  }
}

}  // namespace renderer
}  // namespace mozc
