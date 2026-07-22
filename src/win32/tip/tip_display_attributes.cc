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

#include "win32/tip/tip_display_attributes.h"

#include <windows.h>

#include <cstdint>
#include <memory>
#include <string_view>

#include "absl/base/nullability.h"
#include "base/win32/com.h"
#include "config/config_handler.h"
#include "protocol/config.pb.h"

namespace mozc {
namespace win32 {
namespace tsf {

namespace {

constexpr std::wstring_view kInputDescription =
    L"TextService Display Attribute Input";
constexpr TF_DISPLAYATTRIBUTE kInputAttribute = {
    {TF_CT_NONE, {}},  // text color
    {TF_CT_NONE, {}},  // background color
    TF_LS_DOT,         // underline style
    FALSE,             // underline boldness
    {TF_CT_NONE, {}},  // underline color
    TF_ATTR_INPUT      // attribute info
};

constexpr std::wstring_view kConvertedDescription =
    L"TextService Display Attribute Converted";
constexpr TF_DISPLAYATTRIBUTE kConvertedAttribute = {
    {TF_CT_NONE, {}},         // text color
    {TF_CT_NONE, {}},         // background color
    TF_LS_SOLID,              // underline style
    TRUE,                     // underline boldness
    {TF_CT_NONE, {}},         // underline color
    TF_ATTR_TARGET_CONVERTED  // attribute info
};

COLORREF RgbHexToColorRef(const uint32_t rgb) {
  const BYTE r = static_cast<BYTE>((rgb >> 16) & 0xff);
  const BYTE g = static_cast<BYTE>((rgb >> 8) & 0xff);
  const BYTE b = static_cast<BYTE>(rgb & 0xff);
  return RGB(r, g, b);
}

TF_DA_COLOR NoColor() {
  TF_DA_COLOR color = {};
  color.type = TF_CT_NONE;
  return color;
}

TF_DA_COLOR CustomColor(const uint32_t rgb) {
  TF_DA_COLOR color = {};
  color.type = TF_CT_COLORREF;
  color.cr = RgbHexToColorRef(rgb);
  return color;
}

std::shared_ptr<const config::Config> ReloadAndGetConfig() {
  // The config dialog runs in another process.  Reload here so that
  // GetAttributeInfo() can pick up newly saved values when TSF asks for
  // updated display attributes.
  config::ConfigHandler::Reload();
  return config::ConfigHandler::GetSharedConfig();
}

TF_DISPLAYATTRIBUTE CreateInputAttributeFromConfig() {
  TF_DISPLAYATTRIBUTE attr = kInputAttribute;

  const std::shared_ptr<const config::Config> config = ReloadAndGetConfig();
  if (config == nullptr) {
    return attr;
  }

  attr.crText =
      config->use_custom_preedit_text_color()
          ? CustomColor(config->preedit_text_color())
          : NoColor();

  attr.crBk =
      config->use_custom_preedit_background_color()
          ? CustomColor(config->preedit_background_color())
          : NoColor();

  attr.crLine =
      config->use_custom_preedit_underline_color()
          ? CustomColor(config->preedit_underline_color())
          : NoColor();

  return attr;
}

TF_DISPLAYATTRIBUTE CreateConvertedAttributeFromConfig() {
  TF_DISPLAYATTRIBUTE attr = kConvertedAttribute;

  const std::shared_ptr<const config::Config> config = ReloadAndGetConfig();
  if (config == nullptr) {
    return attr;
  }

  attr.crText =
      config->use_custom_preedit_target_text_color()
          ? CustomColor(config->preedit_target_text_color())
          : NoColor();

  attr.crBk =
      config->use_custom_preedit_target_background_color()
          ? CustomColor(config->preedit_target_background_color())
          : NoColor();

  attr.crLine =
      config->use_custom_preedit_target_underline_color()
          ? CustomColor(config->preedit_target_underline_color())
          : NoColor();

  return attr;
}

#ifdef GOOGLE_JAPANESE_INPUT_BUILD

// {DDF5CDBA-C3FF-4BAF-B817-CC9210FAD27E}
constexpr GUID kDisplayAttributeInput = {
    0xddf5cdba,
    0xc3ff,
    0x4baf,
    {0xb8, 0x17, 0xcc, 0x92, 0x10, 0xfa, 0xd2, 0x7e}};

// {F829C8C0-0EBB-4D29-BD2F-E413A944B7E4}
constexpr GUID kDisplayAttributeConverted = {
    0xf829c8c0,
    0x0ebb,
    0x4d29,
    {0xbd, 0x2f, 0xe4, 0x13, 0xa9, 0x44, 0xb7, 0xe4}};

#else  // GOOGLE_JAPANESE_INPUT_BUILD

// {1A677FDF-21DA-4577-8A18-E77B928CFD2D}
constexpr GUID kDisplayAttributeInput = {
    0x1a677fdf,
    0x21da,
    0x4577,
    {0x8a, 0x18, 0xe7, 0x7b, 0x92, 0x8c, 0xfd, 0x2d}};

// {57FDCD30-8BE0-46F9-B3E5-25BBB17DBB82}
constexpr GUID kDisplayAttributeConverted = {
    0x57fdcd30,
    0x8be0,
    0x46f9,
    {0xb3, 0xe5, 0x25, 0xbb, 0xb1, 0x7d, 0xbb, 0x82}};

#endif  // !GOOGLE_JAPANESE_INPUT_BUILD

}  // namespace

TipDisplayAttribute::TipDisplayAttribute(const GUID& guid,
                                         const TF_DISPLAYATTRIBUTE& attribute,
                                         const std::wstring_view description)
    : guid_(guid),
      description_(description),
      attribute_(attribute),
      original_attribute_(attribute) {}

STDMETHODIMP TipDisplayAttribute::GetGUID(GUID* absl_nullable guid) {
  return SaveToOutParam(guid_, guid);
}

STDMETHODIMP
TipDisplayAttribute::GetDescription(BSTR* absl_nullable description) {
  return SaveToOutParam(MakeUniqueBSTR(description_), description);
}

STDMETHODIMP
TipDisplayAttribute::GetAttributeInfo(
    TF_DISPLAYATTRIBUTE* absl_nullable attribute) {
  return SaveToOutParam(attribute_, attribute);
}

STDMETHODIMP
TipDisplayAttribute::SetAttributeInfo(
    const TF_DISPLAYATTRIBUTE* absl_nullable attribute) {
  if (attribute == nullptr) {
    return E_INVALIDARG;
  }
  attribute_ = *attribute;
  return S_OK;
}

STDMETHODIMP TipDisplayAttribute::Reset() {
  attribute_ = original_attribute_;
  return S_OK;
}

TipDisplayAttributeInput::TipDisplayAttributeInput()
    : TipDisplayAttribute(kDisplayAttributeInput, kInputAttribute,
                          kInputDescription) {}

STDMETHODIMP TipDisplayAttributeInput::GetAttributeInfo(
    TF_DISPLAYATTRIBUTE* absl_nullable attribute) {
  return SaveToOutParam(CreateInputAttributeFromConfig(), attribute);
}

const GUID& TipDisplayAttributeInput::guid() { return kDisplayAttributeInput; }

TipDisplayAttributeConverted::TipDisplayAttributeConverted()
    : TipDisplayAttribute(kDisplayAttributeConverted, kConvertedAttribute,
                          kConvertedDescription) {}

STDMETHODIMP TipDisplayAttributeConverted::GetAttributeInfo(
    TF_DISPLAYATTRIBUTE* absl_nullable attribute) {
  return SaveToOutParam(CreateConvertedAttributeFromConfig(), attribute);
}

const GUID& TipDisplayAttributeConverted::guid() {
  return kDisplayAttributeConverted;
}

}  // namespace tsf
}  // namespace win32
}  // namespace mozc
