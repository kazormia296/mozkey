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

#include "win32/tip/tip_status.h"

#include <msctf.h>
#include <wil/com.h>
#include <wil/resource.h>
#include <windows.h>

#include <utility>

#include "absl/functional/function_ref.h"
#include "base/win32/com.h"
#include "base/win32/hresultor.h"
#include "win32/tip/tip_compartment_util.h"

namespace mozc {
namespace win32 {
namespace tsf {
namespace {

bool SetThreadCompartmentGuarded(ITfThreadMgr* thread_mgr,
                                 const GUID& compartment_guid,
                                 TfClientId client_id,
                                 wil::unique_variant data,
                                 absl::FunctionRef<bool()> is_current) {
  if (thread_mgr == nullptr || !is_current()) {
    return false;
  }
  const auto compartment_manager = ComQuery<ITfCompartmentMgr>(thread_mgr);
  if (!compartment_manager || !is_current()) {
    return false;
  }
  wil::com_ptr_nothrow<ITfCompartment> compartment;
  if (FAILED(compartment_manager->GetCompartment(compartment_guid,
                                                 &compartment)) ||
      compartment == nullptr || !is_current()) {
    return false;
  }
  wil::unique_variant existing_data;
  const HRESULT get_result =
      compartment->GetValue(existing_data.reset_and_addressof());
  if (FAILED(get_result) || !is_current()) {
    return false;
  }
  if (get_result == S_OK &&
      VarCmp(data.addressof(), existing_data.addressof(),
             LOCALE_USER_DEFAULT, 0) == VARCMP_EQ) {
    return true;
  }
  if (!is_current()) {
    return false;
  }
  return SUCCEEDED(compartment->SetValue(client_id, data.addressof())) &&
         is_current();
}

}  // namespace

bool TipStatus::IsOpen(ITfThreadMgr* thread_mgr) {
  // Retrieve the compartment manager from the thread manager, which contains
  // the configuration of the owner thread.
  HResultOr<wil::unique_variant> var =
      TipCompartmentUtil::Get(thread_mgr, GUID_COMPARTMENT_KEYBOARD_OPENCLOSE);

  if (!var.has_value()) {
    return false;
  }
  // Open/Close compartment should be Int32 (I4).
  return var->vt == VT_I4 && var->lVal != FALSE;
}

bool TipStatus::IsDisabledContext(ITfContext* context) {
  // Retrieve the compartment manager from the |context|, which contains the
  // configuration of this context.
  HResultOr<wil::unique_variant> var =
      TipCompartmentUtil::Get(context, GUID_COMPARTMENT_KEYBOARD_DISABLED);
  if (!var.has_value()) {
    return false;
  }
  // Disabled compartment should be Int32 (I4).
  return var->vt == VT_I4 && var->lVal != FALSE;
}

bool TipStatus::IsEmptyContext(ITfContext* context) {
  // Retrieve the compartment manager from the |context|, which contains the
  // configuration of this context.
  HResultOr<wil::unique_variant> var =
      TipCompartmentUtil::Get(context, GUID_COMPARTMENT_EMPTYCONTEXT);
  if (!var.has_value()) {
    return false;
  }
  // Empty context compartment should be Int32 (I4).
  return var->vt == VT_I4 && var->lVal != FALSE;
}

bool TipStatus::GetInputModeConversion(ITfThreadMgr* thread_mgr,
                                       TfClientId client_id, DWORD* mode) {
  if (mode == nullptr) {
    return false;
  }

  constexpr DWORD kDefaultMode =
      TF_CONVERSIONMODE_NATIVE | TF_CONVERSIONMODE_FULLSHAPE;  // Hiragana
  wil::unique_variant default_var;
  default_var.vt = VT_I4;
  default_var.lVal = kDefaultMode;

  HResultOr<wil::unique_variant> var =
      TipCompartmentUtil::GetAndEnsureDataExists(
          thread_mgr, GUID_COMPARTMENT_KEYBOARD_INPUTMODE_CONVERSION, client_id,
          std::move(default_var));
  if (!var.has_value()) {
    return false;
  }

  // Conversion mode compartment should be Int32 (I4).
  if (var->vt != VT_I4) {
    return false;
  }

  *mode = var->lVal;
  return true;
}

bool TipStatus::SetIMEOpen(ITfThreadMgr* thread_mgr, TfClientId client_id,
                           bool open) {
  return SetIMEOpen(thread_mgr, client_id, open, []() { return true; });
}

bool TipStatus::SetIMEOpen(ITfThreadMgr* thread_mgr, TfClientId client_id,
                           bool open,
                           absl::FunctionRef<bool()> is_current) {
  wil::unique_variant var;
  var.vt = VT_I4;
  var.lVal = open ? TRUE : FALSE;
  return SetThreadCompartmentGuarded(
      thread_mgr, GUID_COMPARTMENT_KEYBOARD_OPENCLOSE, client_id,
      std::move(var), is_current);
}

bool TipStatus::SetInputModeConversion(ITfThreadMgr* thread_mgr,
                                       DWORD client_id, DWORD native_mode) {
  return SetInputModeConversion(thread_mgr, client_id, native_mode,
                                []() { return true; });
}

bool TipStatus::SetInputModeConversion(
    ITfThreadMgr* thread_mgr, DWORD client_id, DWORD native_mode,
    absl::FunctionRef<bool()> is_current) {
  wil::unique_variant var;
  var.vt = VT_I4;
  var.lVal = native_mode;
  return SetThreadCompartmentGuarded(
      thread_mgr, GUID_COMPARTMENT_KEYBOARD_INPUTMODE_CONVERSION, client_id,
      std::move(var), is_current);
}

}  // namespace tsf
}  // namespace win32
}  // namespace mozc
