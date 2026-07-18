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

#include "win32/tip/tip_edit_session_impl.h"

#include <inputscope.h>
#include <msctf.h>
#include <wil/com.h>
#include <wil/resource.h>
#include <windows.h>

#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "absl/log/check.h"
#include "base/util.h"
#include "base/win32/com.h"
#include "base/win32/wide_char.h"
#include "client/client_interface.h"
#include "protocol/commands.pb.h"
#include "win32/base/conversion_mode_util.h"
#include "win32/base/input_state.h"
#include "win32/base/string_util.h"
#include "win32/tip/tip_composition_util.h"
#include "win32/tip/tip_edit_session.h"
#include "win32/tip/tip_grimodex_context_util.h"
#include "win32/tip/tip_input_mode_manager.h"
#include "win32/tip/tip_private_context.h"
#include "win32/tip/tip_range_util.h"
#include "win32/tip/tip_status.h"
#include "win32/tip/tip_text_service.h"
#include "win32/tip/tip_thread_context.h"
#include "win32/tip/tip_ui_handler.h"

namespace mozc {
namespace win32 {
namespace tsf {
namespace {

using ::mozc::commands::Output;
using ::mozc::commands::Preedit;
using ::mozc::commands::SessionCommand;
using ::mozc::commands::Status;
using CompositionMode = ::mozc::commands::CompositionMode;
using Segment = ::mozc::commands::Preedit::Segment;
using Annotation = ::mozc::commands::Preedit::Segment::Annotation;

bool IsOutputApplicationCurrent(TipTextService* text_service,
                                ITfContext* context,
                                TsfFocusSnapshot output_domain,
                                uint64_t output_application_generation,
                                bool require_nonsecure) {
  if (!IsTsfContextFocusedForSnapshot(text_service, context, output_domain,
                                      require_nonsecure)) {
    return false;
  }
  const std::shared_ptr<TipPrivateContext> private_context =
      text_service->GetPrivateContext(context);
  return private_context &&
         private_context->IsOutputApplicationForFocusDomain(
             output_domain.focus_epoch, output_domain.focus_revision,
             output_application_generation);
}

HRESULT OnDefaultSelectionUnresolved(TipTextService* text_service,
                                     ITfContext* context,
                                     HRESULT selection_result,
                                     bool* update_ui,
                                     TsfFocusSnapshot edit_domain) {
  if (!IsTsfContextFocusedForSnapshot(text_service, context, edit_domain,
                                      /*require_nonsecure=*/false)) {
    return selection_result;
  }
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_context ||
      !IsTsfFocusSnapshotCurrent(thread_context.get(), edit_domain)) {
    return selection_result;
  }
  TipInputModeManager* input_mode_manager =
      thread_context->GetInputModeManager();
  const auto action = input_mode_manager->OnInputScopeUnresolved();
  if (update_ui != nullptr &&
      (action == TipInputModeManager::kUpdateUI ||
       input_mode_manager->IsIndicatorVisible())) {
    *update_ui = true;
  }

  // Selection resolution is a prerequisite for authoritative InputScope.
  // Notify the server of the temporary password domain even when the manager
  // was already fail-closed, so no previous non-secure session can survive the
  // unresolved edit.
  text_service->SetRendererCallbackProvenance(
      /*token=*/0, /*focus_epoch=*/0, /*focus_revision=*/0,
      /*output_generation=*/0);
  TipUiHandler::OnFocusChange(text_service, nullptr, edit_domain.focus_epoch,
                              edit_domain.focus_revision);
  if (!IsTsfContextFocusedForSnapshot(text_service, context, edit_domain,
                                      /*require_nonsecure=*/false) ||
      text_service->GetThreadContextLease().get() != thread_context.get()) {
    return selection_result;
  }
  const std::shared_ptr<TipPrivateContext> private_context =
      text_service->GetPrivateContext(context);
  if (private_context != nullptr &&
      !SendTsfResetContextForProgram(
          text_service, /*program=*/{}, private_context->GetClient(),
          /*secure_input=*/true)) {
    return E_FAIL;
  }
  return selection_result;
}

HRESULT SetReadingProperties(TipTextService* text_service, ITfContext* context,
                             ITfRange* range,
                             const std::string& reading_string_utf8,
                             TfEditCookie write_cookie,
                             TsfFocusSnapshot output_domain,
                             uint64_t output_application_generation) {
  const auto is_current = [&]() {
    return IsOutputApplicationCurrent(
        text_service, context, output_domain, output_application_generation,
        /*require_nonsecure=*/true);
  };
  if (!is_current()) {
    return S_FALSE;
  }
  HRESULT result = S_OK;

  // Get out the reading property
  wil::com_ptr_nothrow<ITfProperty> reading_property;
  result = context->GetProperty(GUID_PROP_READING, &reading_property);
  if (FAILED(result)) {
    return result;
  }
  if (!is_current()) {
    return S_FALSE;
  }

  const std::wstring& canonical_reading_string =
      StringUtil::KeyToReading(reading_string_utf8);
  wil::unique_variant reading =
      wil::make_variant_bstr_nothrow(canonical_reading_string.c_str());
  if (!is_current()) {
    return S_FALSE;
  }
  result = reading_property->SetValue(write_cookie, range, reading.addressof());
  return is_current() ? result : S_FALSE;
}

HRESULT ClearReadingProperties(TipTextService* text_service,
                               ITfContext* context, ITfRange* range,
                               TfEditCookie write_cookie,
                               TsfFocusSnapshot output_domain,
                               uint64_t output_application_generation) {
  const auto is_current = [&]() {
    return IsOutputApplicationCurrent(
        text_service, context, output_domain, output_application_generation,
        /*require_nonsecure=*/true);
  };
  if (!is_current()) {
    return S_FALSE;
  }
  HRESULT result = S_OK;

  // Get out the reading property
  wil::com_ptr_nothrow<ITfProperty> reading_property;
  result = context->GetProperty(GUID_PROP_READING, &reading_property);
  if (FAILED(result)) {
    return result;
  }
  if (!is_current()) {
    return S_FALSE;
  }
  // Clear existing attributes.
  if (!is_current()) {
    return S_FALSE;
  }
  result = reading_property->Clear(write_cookie, range);
  if (FAILED(result)) {
    return result;
  }
  return is_current() ? result : S_FALSE;
}

wil::com_ptr_nothrow<ITfComposition> CreateComposition(
    TipTextService* text_service, ITfContext* context,
    TfEditCookie write_cookie, TsfFocusSnapshot output_domain,
    uint64_t output_application_generation,
    uint64_t* composition_generation) {
  if (composition_generation == nullptr) {
    return nullptr;
  }
  *composition_generation = 0;
  const auto is_current = [&]() {
    return IsOutputApplicationCurrent(
        text_service, context, output_domain, output_application_generation,
        /*require_nonsecure=*/true);
  };
  if (!is_current()) {
    return nullptr;
  }
  auto composition_context = ComQuery<ITfContextComposition>(context);
  if (!composition_context || !is_current()) {
    return nullptr;
  }
  auto insert_selection = ComQuery<ITfInsertAtSelection>(context);
  if (!insert_selection || !is_current()) {
    return nullptr;
  }
  wil::com_ptr_nothrow<ITfRange> insertion_pos;
  if (FAILED(insert_selection->InsertTextAtSelection(
          write_cookie, TF_IAS_QUERYONLY, nullptr, 0, &insertion_pos)) ||
      !is_current()) {
    return nullptr;
  }
  wil::com_ptr_nothrow<ITfComposition> composition;
  const std::shared_ptr<TipPrivateContext> private_context =
      text_service->GetPrivateContext(context);
  if (!private_context || !is_current()) {
    return nullptr;
  }
  const uint64_t reserved_generation =
      private_context->BeginCompositionForFocusDomain(
          output_domain.focus_epoch, output_domain.focus_revision);
  const wil::com_ptr_nothrow<ITfCompositionSink> composition_sink =
      text_service->CreateCompositionSink(
          context, output_domain.focus_epoch, output_domain.focus_revision,
          reserved_generation);
  if (!composition_sink || !is_current()) {
    private_context->ClearCompositionForFocusDomainAndGeneration(
        output_domain.focus_epoch, output_domain.focus_revision,
        reserved_generation);
    return nullptr;
  }
  const HRESULT start_result = composition_context->StartComposition(
      write_cookie, insertion_pos.get(), composition_sink.get(), &composition);
  if (FAILED(start_result) || !is_current() ||
      !private_context->IsCompositionForFocusDomainAndGeneration(
          output_domain.focus_epoch, output_domain.focus_revision,
          reserved_generation)) {
    private_context->ClearCompositionForFocusDomainAndGeneration(
        output_domain.focus_epoch, output_domain.focus_revision,
        reserved_generation);
    return nullptr;
  }
  *composition_generation = reserved_generation;
  return composition;
}

// Note: Committing a text is a tricky part in TSF/CUAS. Basically it should be
// done as following steps.
//   1. Create a composition (if not exists).
//   2. Replace the text stored in the composition range with the text to be
//      committed. Note that CUAS updates GCS_RESULTCLAUSE and
//      GCS_RESULTREADCLAUSE by using the segment structure of GUID_PROP_READING
//      property. For example, CUAS generates two segments for the following
//      reading text structure.
//        "今日は(きょうは)/晴天(せいてん)"
//   3. Call ITfComposition::ShiftStart to shrink the composition range. Note
//      that the text that is pushed out from the composition range is
//      interpreted as the "committed text".
//   4. Update the caret position explicitly. Note that some applications
//      such as WPF's TextBox do not update the caret position automatically
//      when a composition is committed.
// See also b/8406545 and b/9747361.
wil::com_ptr_nothrow<ITfComposition> CommitText(
    TipTextService* text_service, ITfContext* context,
    TfEditCookie write_cookie, wil::com_ptr_nothrow<ITfComposition> composition,
    const Output& output, TsfFocusSnapshot output_domain,
    uint64_t output_application_generation,
    uint64_t* composition_generation) {
  if (composition_generation == nullptr) {
    return nullptr;
  }
  const auto is_current = [&]() {
    if (!IsOutputApplicationCurrent(
            text_service, context, output_domain,
            output_application_generation,
            /*require_nonsecure=*/true)) {
      return false;
    }
    if (!composition) {
      return *composition_generation == 0;
    }
    const std::shared_ptr<TipPrivateContext> private_context =
        text_service->GetPrivateContext(context);
    return private_context &&
           private_context->IsCompositionForFocusDomainAndGeneration(
               output_domain.focus_epoch, output_domain.focus_revision,
               *composition_generation);
  };
  if (!is_current()) {
    return nullptr;
  }
  if (!composition) {
    composition =
        CreateComposition(text_service, context, write_cookie, output_domain,
                          output_application_generation,
                          composition_generation);
    if (!composition || !is_current()) {
      return nullptr;
    }
  }

  HRESULT result = S_OK;

  wil::com_ptr_nothrow<ITfRange> composition_range;
  result = composition->GetRange(&composition_range);
  if (FAILED(result) || !is_current()) {
    return nullptr;
  }

  std::wstring composition_text;
  TipRangeUtil::GetText(composition_range.get(), write_cookie,
                        &composition_text);
  if (!is_current()) {
    return nullptr;
  }

  // Make sure that |composition_text| begins with |result_text| so that
  // CUAS can generate an appropriate GCS_RESULTREADCLAUSE information.
  // See b/8406545
  const std::wstring result_text = Utf8ToWide(output.result().value());
  if (composition_text.find(result_text) != 0) {
    if (!is_current()) {
      return nullptr;
    }
    result = composition_range->SetText(write_cookie, 0, result_text.c_str(),
                                        result_text.size());
    if (FAILED(result) || !is_current()) {
      return nullptr;
    }
    result = SetReadingProperties(text_service, context,
                                  composition_range.get(),
                                  output.result().key(), write_cookie,
                                  output_domain,
                                  output_application_generation);
    if (FAILED(result) || result == S_FALSE || !is_current()) {
      return nullptr;
    }
  }

  wil::com_ptr_nothrow<ITfRange> new_composition_start;
  result = composition_range->Clone(&new_composition_start);
  if (FAILED(result) || !is_current()) {
    return nullptr;
  }
  LONG moved = 0;
  result = new_composition_start->ShiftStart(write_cookie, result_text.size(),
                                             &moved, nullptr);
  if (FAILED(result) || !is_current()) {
    return nullptr;
  }
  result = new_composition_start->Collapse(write_cookie, TF_ANCHOR_START);
  if (FAILED(result) || !is_current()) {
    return nullptr;
  }
  result = composition->ShiftStart(write_cookie, new_composition_start.get());
  if (FAILED(result) || !is_current()) {
    return nullptr;
  }
  // We need to update the caret position manually for WPF's TextBox, where
  // caret position is not updated automatically when a composition text is
  // committed by ITfComposition::ShiftStart.
  result = TipRangeUtil::SetSelection(context, write_cookie,
                                      new_composition_start.get(), TF_AE_END);
  if (FAILED(result) || !is_current()) {
    return nullptr;
  }
  return composition;
}

HRESULT UpdateComposition(TipTextService* text_service, ITfContext* context,
                          wil::com_ptr_nothrow<ITfComposition> composition,
                          TfEditCookie write_cookie, const Output& output,
                          TsfFocusSnapshot output_domain,
                          uint64_t output_application_generation,
                          uint64_t composition_generation) {
  const auto is_current = [&]() {
    if (!IsOutputApplicationCurrent(
            text_service, context, output_domain,
            output_application_generation,
            /*require_nonsecure=*/true)) {
      return false;
    }
    if (!composition) {
      return composition_generation == 0;
    }
    const std::shared_ptr<TipPrivateContext> private_context =
        text_service->GetPrivateContext(context);
    return private_context &&
           private_context->IsCompositionForFocusDomainAndGeneration(
               output_domain.focus_epoch, output_domain.focus_revision,
               composition_generation);
  };
  const auto validate = [&](HRESULT value) {
    if (FAILED(value)) {
      return value;
    }
    return is_current() ? value : S_FALSE;
  };
  if (!is_current()) {
    return S_FALSE;
  }
  HRESULT result = S_OK;

  if (!output.has_preedit()) {
    if (composition) {
      wil::com_ptr_nothrow<ITfRange> composition_range;
      result = validate(composition->GetRange(&composition_range));
      if (result != S_OK) {
        return result;
      }
      BOOL is_empty = FALSE;
      result = validate(composition_range->IsEmpty(write_cookie, &is_empty));
      if (result != S_OK) {
        return result;
      }
      if (is_empty != TRUE) {
        std::wstring str;
        TipRangeUtil::GetText(composition_range.get(), write_cookie, &str);
        if (!is_current()) {
          return S_FALSE;
        }
        result = validate(
            composition_range->SetText(write_cookie, 0, L"", 0));
        if (result != S_OK) {
          return result;
        }
        result = ClearReadingProperties(text_service, context,
                                        composition_range.get(), write_cookie,
                                        output_domain,
                                        output_application_generation);
        if (result != S_OK) {
          return result;
        }
      }
      if (!is_current()) {
        return S_FALSE;
      }
      result = composition->EndComposition(write_cookie);
      if (FAILED(result)) {
        return result;
      }
      if (!IsOutputApplicationCurrent(
              text_service, context, output_domain,
              output_application_generation,
              /*require_nonsecure=*/true)) {
        return S_FALSE;
      }
      const std::shared_ptr<TipPrivateContext> private_context =
          text_service->GetPrivateContext(context);
      if (!private_context ||
          !IsOutputApplicationCurrent(
              text_service, context, output_domain,
              output_application_generation,
              /*require_nonsecure=*/true)) {
        return S_FALSE;
      }
      if (private_context->IsCompositionForFocusDomainAndGeneration(
              output_domain.focus_epoch, output_domain.focus_revision,
              composition_generation)) {
        private_context->ClearCompositionForFocusDomainAndGeneration(
            output_domain.focus_epoch, output_domain.focus_revision,
            composition_generation);
      } else if (!private_context->IsLatestCompositionGenerationInactive(
                     composition_generation)) {
        return S_FALSE;
      }
      composition.reset();
      composition_generation = 0;
    }
    return is_current() ? S_OK : S_FALSE;
  }

  DCHECK(output.has_preedit());

  if (!composition) {
    auto insert_selection = ComQuery<ITfInsertAtSelection>(context);
    if (!insert_selection) {
      return E_FAIL;
    }
    if (!is_current()) {
      return S_FALSE;
    }
    wil::com_ptr_nothrow<ITfRange> insertion_pos;
    result = validate(insert_selection->InsertTextAtSelection(
        write_cookie, TF_IAS_QUERYONLY, nullptr, 0, &insertion_pos));
    if (result != S_OK) {
      return result;
    }
    composition =
        CreateComposition(text_service, context, write_cookie, output_domain,
                          output_application_generation,
                          &composition_generation);
    if (!composition) {
      return is_current() ? E_FAIL : S_FALSE;
    }
  }
  wil::com_ptr_nothrow<ITfRange> composition_range;
  result = validate(composition->GetRange(&composition_range));
  if (result != S_OK) {
    return result;
  }

  const Preedit& preedit = output.preedit();
  const std::wstring& preedit_text = StringUtil::ComposePreeditText(preedit);
  result = validate(composition_range->SetText(
      write_cookie, 0, preedit_text.c_str(), preedit_text.size()));
  if (result != S_OK) {
    return result;
  }

  // Get out the display attribute property
  wil::com_ptr_nothrow<ITfProperty> display_attribute;
  result = validate(
      context->GetProperty(GUID_PROP_ATTRIBUTE, &display_attribute));
  if (result != S_OK) {
    return result;
  }

  // Get out the reading property
  wil::com_ptr_nothrow<ITfProperty> reading_property;
  result =
      validate(context->GetProperty(GUID_PROP_READING, &reading_property));
  if (result != S_OK) {
    return result;
  }

  // Set each segment's display attribute
  int start = 0;
  int end = 0;
  for (int i = 0; i < preedit.segment_size(); ++i) {
    const Preedit::Segment& segment = preedit.segment(i);
    end = start + WideCharsLen(segment.value());
    const Preedit::Segment::Annotation& annotation = segment.annotation();
    TfGuidAtom attribute = TF_INVALID_GUIDATOM;
    if (annotation == Preedit::Segment::UNDERLINE) {
      attribute = text_service->input_attribute();
    } else if (annotation == Preedit::Segment::HIGHLIGHT) {
      attribute = text_service->converted_attribute();
    } else {  // mozc::commands::Preedit::Segment::NONE or unknown value
      start = end;
      continue;
    }

    wil::com_ptr_nothrow<ITfRange> segment_range;
    result = validate(composition_range->Clone(&segment_range));
    if (result != S_OK) {
      return result;
    }
    result =
        validate(segment_range->Collapse(write_cookie, TF_ANCHOR_START));
    if (result != S_OK) {
      return result;
    }
    LONG shift = 0;
    result = validate(
        segment_range->ShiftEnd(write_cookie, end, &shift, nullptr));
    if (result != S_OK) {
      return result;
    }
    result = validate(
        segment_range->ShiftStart(write_cookie, start, &shift, nullptr));
    if (result != S_OK) {
      return result;
    }
    wil::unique_variant var;
    // set the value over the range
    var.vt = VT_I4;
    var.lVal = attribute;
    result = validate(display_attribute->SetValue(
        write_cookie, segment_range.get(), var.addressof()));
    if (result != S_OK) {
      return result;
    }
    if (segment.has_key()) {
      const std::wstring& reading_string =
          StringUtil::KeyToReading(segment.key());
      wil::unique_variant reading =
          wil::make_variant_bstr_nothrow(reading_string.c_str());
      result = validate(reading_property->SetValue(
          write_cookie, segment_range.get(), reading.addressof()));
      if (result != S_OK) {
        return result;
      }
    }
    start = end;
  }

  // Update cursor.
  {
    std::string preedit_text;
    for (int i = 0; i < preedit.segment_size(); ++i) {
      preedit_text += preedit.segment(i).value();
    }

    wil::com_ptr_nothrow<ITfRange> cursor_range;
    result = validate(composition_range->Clone(&cursor_range));
    if (result != S_OK) {
      return result;
    }
    // |output.preedit().cursor()| is in the unit of UTF-32. We need to convert
    // it to UTF-16 for TSF.
    const uint32_t cursor_pos_utf16 =
        WideCharsLen(Util::Utf8SubString(preedit_text, 0, preedit.cursor()));

    result = validate(cursor_range->Collapse(write_cookie, TF_ANCHOR_START));
    if (result != S_OK) {
      return result;
    }
    LONG shift = 0;
    result = validate(cursor_range->ShiftEnd(
        write_cookie, cursor_pos_utf16, &shift, nullptr));
    if (result != S_OK) {
      return result;
    }
    result = validate(cursor_range->ShiftStart(
        write_cookie, cursor_pos_utf16, &shift, nullptr));
    if (result != S_OK) {
      return result;
    }
    result = validate(TipRangeUtil::SetSelection(
        context, write_cookie, cursor_range.get(), TF_AE_END));
  }
  return result;
}

HRESULT UpdatePrivateContext(TipTextService* text_service, ITfContext* context,
                             TfEditCookie write_cookie, const Output& output,
                             TsfFocusSnapshot output_domain,
                             uint64_t output_application_generation) {
  if (!IsOutputApplicationCurrent(
          text_service, context, output_domain,
          output_application_generation,
          /*require_nonsecure=*/false)) {
    return S_FALSE;
  }
  // The visible renderer still carries a token for the previous output.
  // Revoke it before replacing last_output so a reentrant click cannot be
  // membership-checked against candidate IDs that were never shown with that
  // token.  UpdateCommand publishes a fresh token for the new output.
  text_service->SetRendererCallbackProvenance(
      /*token=*/0, /*focus_epoch=*/0, /*focus_revision=*/0,
      /*output_generation=*/0);
  const std::shared_ptr<TipPrivateContext> private_context =
      text_service->GetPrivateContext(context);
  if (private_context == nullptr) {
    return S_FALSE;
  }
  private_context->SetLastOutputForFocusDomain(
      output, output_domain.focus_epoch, output_domain.focus_revision);
  if (!IsOutputApplicationCurrent(
          text_service, context, output_domain,
          output_application_generation,
          /*require_nonsecure=*/false)) {
    return S_FALSE;
  }
  if (!output.has_status()) {
    return S_FALSE;
  }

  const Status& status = output.status();
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  wil::com_ptr_nothrow<ITfThreadMgr> thread_manager =
      text_service->GetThreadManager();
  if (!thread_context || !thread_manager) {
    return S_FALSE;
  }
  TipInputModeManager* input_mode_manager =
      thread_context->GetInputModeManager();
  const auto is_output_application_current = [&]() {
    return IsOutputApplicationCurrent(
        text_service, context, output_domain, output_application_generation,
        /*require_nonsecure=*/false);
  };
  const TipInputModeManager::NotifyActionSet action_set =
      input_mode_manager->OnReceiveCommand(
          status.activated(), status.comeback_mode(), status.mode());
  if (!IsOutputApplicationCurrent(
          text_service, context, output_domain,
          output_application_generation,
          /*require_nonsecure=*/false)) {
    return S_FALSE;
  }
  if ((action_set & TipInputModeManager::kNotifySystemOpenClose) ==
      TipInputModeManager::kNotifySystemOpenClose) {
    TipStatus::SetIMEOpen(thread_manager.get(),
                          text_service->GetClientID(),
                          input_mode_manager->GetEffectiveOpenClose(),
                          is_output_application_current);
    if (!IsOutputApplicationCurrent(
            text_service, context, output_domain,
            output_application_generation,
            /*require_nonsecure=*/false)) {
      return S_FALSE;
    }
  }

  if ((action_set & TipInputModeManager::kNotifySystemConversionMode) ==
      TipInputModeManager::kNotifySystemConversionMode) {
    const CompositionMode mozc_mode = static_cast<CompositionMode>(
        input_mode_manager->GetEffectiveConversionMode());
    uint32_t native_mode = 0;
    if (ConversionModeUtil::ToNativeMode(
            mozc_mode, private_context->input_behavior().prefer_kana_input,
            &native_mode)) {
      TipStatus::SetInputModeConversion(thread_manager.get(),
                                        text_service->GetClientID(),
                                        native_mode,
                                        is_output_application_current);
      if (!IsOutputApplicationCurrent(
              text_service, context, output_domain,
              output_application_generation,
              /*require_nonsecure=*/false)) {
        return S_FALSE;
      }
    }
  }
  return S_OK;
}

HRESULT UpdatePreeditAndComposition(TipTextService* text_service,
                                    ITfContext* context,
                                    TfEditCookie write_cookie,
                                    const Output& output,
                                    TsfFocusSnapshot output_domain,
                                    uint64_t output_application_generation) {
  if (!IsOutputApplicationCurrent(
          text_service, context, output_domain,
          output_application_generation,
          /*require_nonsecure=*/true)) {
    return S_FALSE;
  }
  const std::shared_ptr<TipPrivateContext> private_context =
      text_service->GetPrivateContext(context);
  if (!private_context) {
    return S_FALSE;
  }
  const uint64_t initial_composition_generation =
      private_context->composition_generation();
  uint64_t composition_generation = initial_composition_generation;
  wil::com_ptr_nothrow<ITfComposition> composition =
      TipCompositionUtil::GetComposition(context, write_cookie);
  if (!IsOutputApplicationCurrent(
          text_service, context, output_domain,
          output_application_generation,
          /*require_nonsecure=*/true)) {
    return S_FALSE;
  }
  if ((composition &&
       !private_context->IsCompositionForFocusDomainAndGeneration(
           output_domain.focus_epoch, output_domain.focus_revision,
           composition_generation)) ||
      (!composition && composition_generation != 0)) {
    return S_FALSE;
  }

  // Clear the display attributes first.
  // TODO(https://github.com/google/mozc/discussions/1388): Revisit here.
  if (composition) {
    const auto is_composition_application_current = [&]() {
      return IsOutputApplicationCurrent(
                 text_service, context, output_domain,
                 output_application_generation,
                 /*require_nonsecure=*/true) &&
             private_context->IsCompositionForFocusDomainAndGeneration(
                 output_domain.focus_epoch, output_domain.focus_revision,
                 composition_generation);
    };
    const HRESULT result = TipCompositionUtil::ClearDisplayAttributes(
        context, composition.get(), write_cookie,
        is_composition_application_current);
    if (FAILED(result)) {
      return result;
    }
    if (!IsOutputApplicationCurrent(
            text_service, context, output_domain,
            output_application_generation,
            /*require_nonsecure=*/true) ||
        !private_context->IsCompositionForFocusDomainAndGeneration(
            output_domain.focus_epoch, output_domain.focus_revision,
            composition_generation)) {
      return S_FALSE;
    }
  }

  if (output.has_result()) {
    composition = CommitText(text_service, context, write_cookie,
                             std::move(composition), output, output_domain,
                             output_application_generation,
                             &composition_generation);
    if (!composition) {
      return IsOutputApplicationCurrent(
                 text_service, context, output_domain,
                 output_application_generation,
                 /*require_nonsecure=*/true)
                 ? E_FAIL
                 : S_FALSE;
    }
    if (!IsOutputApplicationCurrent(
            text_service, context, output_domain,
            output_application_generation,
            /*require_nonsecure=*/true)) {
      return S_FALSE;
    }
  }

  const HRESULT result = UpdateComposition(text_service, context,
                                           std::move(composition), write_cookie,
                                           output, output_domain,
                                           output_application_generation,
                                           composition_generation);
  if (!IsOutputApplicationCurrent(
          text_service, context, output_domain,
          output_application_generation,
          /*require_nonsecure=*/true)) {
    return S_FALSE;
  }
  return result;
}

HRESULT DoEditSessionInComposition(TipTextService* text_service,
                                   ITfContext* context,
                                   TfEditCookie write_cookie,
                                   const Output& output,
                                   TsfFocusSnapshot output_domain,
                                   uint64_t output_application_generation) {
  const HRESULT result = UpdatePrivateContext(
      text_service, context, write_cookie, output, output_domain,
      output_application_generation);
  if (FAILED(result)) {
    return result;
  }
  if (!IsOutputApplicationCurrent(
          text_service, context, output_domain,
          output_application_generation,
          /*require_nonsecure=*/false)) {
    return S_FALSE;
  }
  return UpdatePreeditAndComposition(text_service, context, write_cookie,
                                     output, output_domain,
                                     output_application_generation);
}

HRESULT DoEditSessionAfterComposition(TipTextService* text_service,
                                      ITfContext* context,
                                      TfEditCookie write_cookie,
                                      const Output& output,
                                      TsfFocusSnapshot output_domain,
                                      uint64_t output_application_generation) {
  return UpdatePrivateContext(text_service, context, write_cookie, output,
                              output_domain,
                              output_application_generation);
}

HRESULT OnEndEditImpl(TipTextService* text_service, ITfContext* context,
                      TfEditCookie write_cookie, ITfEditRecord* edit_record,
                      bool* update_ui) {
  bool dummy_bool = false;
  if (update_ui == nullptr) {
    update_ui = &dummy_bool;
  }
  *update_ui = false;
  const TsfFocusSnapshot edit_domain =
      CaptureTsfFocusSnapshot(text_service->GetThreadContext());
  if (!IsTsfContextFocusedForSnapshot(text_service, context, edit_domain,
                                      /*require_nonsecure=*/false)) {
    return S_OK;
  }

  HRESULT result = S_OK;

  {
    wil::com_ptr_nothrow<ITfRange> selection_range;
    TfActiveSelEnd active_sel_end = TF_AE_NONE;
    result = TipRangeUtil::GetDefaultSelection(
        context, write_cookie, &selection_range, &active_sel_end);
    if (FAILED(result)) {
      return OnDefaultSelectionUnresolved(text_service, context, result,
                                          update_ui, edit_domain);
    }
    std::vector<InputScope> input_scopes;
    result = TipRangeUtil::GetInputScopes(selection_range.get(), write_cookie,
                                          &input_scopes);
    if (FAILED(result)) {
      // Scope lookup failure is security-significant: treat the field as a
      // password until TSF supplies an authoritative non-password scope.
      input_scopes = {IS_PASSWORD};
    }
    if (!IsTsfContextFocusedForSnapshot(text_service, context, edit_domain,
                                        /*require_nonsecure=*/false)) {
      return S_OK;
    }
    const std::shared_ptr<TipThreadContext> scope_thread_context =
        text_service->GetThreadContextLease();
    if (!scope_thread_context) {
      return S_OK;
    }
    TipInputModeManager* input_mode_manager =
        scope_thread_context->GetInputModeManager();
    const bool was_password = input_mode_manager->IsPasswordInputScope();
    const auto actions = input_mode_manager->OnChangeInputScope(input_scopes);
    const bool is_password = input_mode_manager->IsPasswordInputScope();
    if (was_password != is_password) {
      if (is_password) {
        text_service->SetRendererCallbackProvenance(
            /*token=*/0, /*focus_epoch=*/0, /*focus_revision=*/0,
            /*output_generation=*/0);
        TipUiHandler::OnFocusChange(text_service, nullptr,
                                    edit_domain.focus_epoch,
                                    edit_domain.focus_revision);
      }
      const std::shared_ptr<TipPrivateContext> private_context =
          text_service->GetPrivateContext(context);
      if (private_context != nullptr &&
          !SendTsfResetContext(text_service, context,
                               private_context->GetClient())) {
        return E_FAIL;
      }
    }
    if (text_service->GetThreadContextLease().get() !=
        scope_thread_context.get()) {
      return S_OK;
    }
    if (actions == TipInputModeManager::kUpdateUI) {
      *update_ui = true;
    }
    // If the indicator is visible, update UI just in case.
    if (input_mode_manager->IsIndicatorVisible()) {
      *update_ui = true;
    }
  }

  const std::shared_ptr<TipPrivateContext> composition_private_context =
      text_service->GetPrivateContext(context);
  const uint64_t composition_generation =
      composition_private_context
          ? composition_private_context->composition_generation()
          : 0;
  const auto is_exact_composition_current = [&]() {
    return composition_private_context &&
           IsTsfContextFocusedForSnapshot(
               text_service, context, edit_domain,
               /*require_nonsecure=*/true) &&
           composition_private_context
               ->IsCompositionForFocusDomainAndGeneration(
                   edit_domain.focus_epoch, edit_domain.focus_revision,
                   composition_generation);
  };
  if (!is_exact_composition_current()) {
    return S_OK;
  }
  wil::com_ptr_nothrow<ITfComposition> composition =
      TipCompositionUtil::GetComposition(context, write_cookie);
  if (!is_exact_composition_current()) {
    return S_OK;
  }
  if (!composition) {
    // Nothing to do.
    return S_OK;
  }

  wil::com_ptr_nothrow<ITfRange> composition_range;
  result = composition->GetRange(&composition_range);
  if (FAILED(result)) {
    return result;
  }
  if (!is_exact_composition_current()) {
    return S_OK;
  }

  BOOL selection_changed = FALSE;
  result = edit_record->GetSelectionStatus(&selection_changed);
  if (FAILED(result)) {
    return result;
  }
  if (!is_exact_composition_current()) {
    return S_OK;
  }
  if (selection_changed) {
    // When the selection is changed, make sure the new selection range is
    // covered by the composition range. Otherwise, terminate the composition.
    wil::com_ptr_nothrow<ITfRange> selected_range;
    TfActiveSelEnd active_sel_end = TF_AE_NONE;
    result = TipRangeUtil::GetDefaultSelection(
        context, write_cookie, &selected_range, &active_sel_end);
    if (FAILED(result)) {
      if (!is_exact_composition_current()) {
        return S_OK;
      }
      return OnDefaultSelectionUnresolved(text_service, context, result,
                                          update_ui, edit_domain);
    }
    if (!is_exact_composition_current()) {
      return S_OK;
    }
    const bool selection_is_covered = TipRangeUtil::IsRangeCovered(
        write_cookie, selected_range.get(), composition_range.get());
    if (!is_exact_composition_current()) {
      return S_OK;
    }
    if (!selection_is_covered) {
      // We enqueue another edit session to sync the composition state between
      // the application and Mozc server because we are already in
      // ITfTextEditSink::OnEndEdit and some operations (e.g.,
      // ITfComposition::EndComposition) result in failure in this edit
      // session.
      commands::SessionCommand command;
      command.set_type(commands::SessionCommand::SUBMIT);
      if (!is_exact_composition_current() ||
          !TipEditSession::SendCompositionSessionCommandAsync(
              text_service, context, command, edit_domain.focus_epoch,
              edit_domain.focus_revision, composition_generation)) {
        return E_FAIL;
      }
      // Cancels further operations.
      return S_OK;
    }
  }

  BOOL is_empty = FALSE;
  result = composition_range->IsEmpty(write_cookie, &is_empty);
  if (FAILED(result)) {
    return result;
  }
  if (!is_exact_composition_current()) {
    return S_OK;
  }
  if (is_empty) {
    // When the composition range is empty, we assume the composition is
    // canceled by the application or something. Actually CUAS does this when
    // it receives NI_COMPOSITIONSTR/CPS_CANCEL. You can see this as Excel's
    // auto-completion. If this happens, send REVERT command to the server to
    // keep the state consistent. See b/1793331 for details.

    // We enqueue another edit session to sync the composition state between
    // the application and Mozc server because we are already in
    // ITfTextEditSink::OnEndEdit and some operations (e.g.,
    // ITfComposition::EndComposition) result in failure in this edit session.
    commands::SessionCommand command;
    command.set_type(commands::SessionCommand::REVERT);
    const bool scheduled = is_exact_composition_current() &&
        TipEditSession::SendCompositionSessionCommandAsync(
        text_service, context, command, edit_domain.focus_epoch,
        edit_domain.focus_revision, composition_generation);
    *update_ui = false;
    if (!scheduled) {
      return E_FAIL;
    }
  }
  return S_OK;
}

}  // namespace

HRESULT TipEditSessionImpl::OnEndEdit(TipTextService* text_service,
                                      ITfContext* context,
                                      TfEditCookie write_cookie,
                                      ITfEditRecord* edit_record) {
  const TsfFocusSnapshot ui_domain =
      CaptureTsfFocusSnapshot(text_service->GetThreadContext());
  bool update_ui = false;
  const HRESULT result = OnEndEditImpl(text_service, context, write_cookie,
                                       edit_record, &update_ui);
  if (update_ui) {
    TipEditSessionImpl::UpdateUI(text_service, context, write_cookie,
                                 ui_domain.focus_epoch,
                                 ui_domain.focus_revision);
  }
  return result;
}

HRESULT TipEditSessionImpl::OnCompositionTerminated(
    TipTextService* text_service, ITfContext* context,
    ITfComposition* composition, TfEditCookie write_cookie,
    uint64_t composition_focus_epoch,
    int32_t composition_focus_revision,
    uint64_t composition_generation) {
  if (text_service == nullptr) {
    return E_FAIL;
  }
  if (context == nullptr) {
    return E_FAIL;
  }
  const TsfFocusSnapshot output_domain = {
      .focus_epoch = composition_focus_epoch,
      .focus_revision = composition_focus_revision,
  };
  if (!IsTsfContextFocusedForSnapshot(text_service, context, output_domain,
                                      /*require_nonsecure=*/true)) {
    return S_OK;
  }
  const std::shared_ptr<TipPrivateContext> private_context =
      text_service->GetPrivateContext(context);
  if (!private_context ||
      !private_context->ClearCompositionForFocusDomainAndGeneration(
          output_domain.focus_epoch, output_domain.focus_revision,
          composition_generation)) {
    return S_OK;
  }

  // Clear the display attributes first.
  // TODO(https://github.com/google/mozc/discussions/1388): Revisit here.
  if (composition) {
    const auto is_terminated_composition_current = [&]() {
      return IsTsfContextFocusedForSnapshot(
                 text_service, context, output_domain,
                 /*require_nonsecure=*/true) &&
             private_context->IsLatestCompositionGenerationInactive(
                 composition_generation);
    };
    const HRESULT result = TipCompositionUtil::ClearDisplayAttributes(
        context, composition, write_cookie,
        is_terminated_composition_current);
    if (FAILED(result)) {
      return result;
    }
    if (!IsTsfContextFocusedForSnapshot(text_service, context, output_domain,
                                        /*require_nonsecure=*/true) ||
        !private_context->IsLatestCompositionGenerationInactive(
            composition_generation)) {
      return S_OK;
    }
  }

  if (!private_context->IsLatestCompositionGenerationInactive(
          composition_generation)) {
    return S_OK;
  }

  SessionCommand command;
  command.set_type(SessionCommand::SUBMIT);
  Output output;
  uint64_t output_application_generation = 0;
  if (!SendTsfCommand(text_service, context, private_context->GetClient(),
                      command, &output, output_domain,
                      /*require_nonsecure=*/true,
                      &output_application_generation)) {
    return E_FAIL;
  }
  if (!IsTsfContextFocusedForSnapshot(text_service, context, output_domain,
                                      /*require_nonsecure=*/true) ||
      !private_context->IsLatestCompositionGenerationInactive(
          composition_generation)) {
    return S_OK;
  }
  const HRESULT result = DoEditSessionAfterComposition(
      text_service, context, write_cookie, output, output_domain,
      output_application_generation);
  if (IsOutputApplicationCurrent(
          text_service, context, output_domain,
          output_application_generation,
          /*require_nonsecure=*/true)) {
    UpdateUI(text_service, context, write_cookie, output_domain.focus_epoch,
             output_domain.focus_revision);
  }
  return result;
}

HRESULT TipEditSessionImpl::UpdateContext(TipTextService* text_service,
                                          ITfContext* context,
                                          TfEditCookie write_cookie,
                                          const commands::Output& output,
                                          uint64_t output_focus_epoch,
                                          int32_t output_focus_revision,
                                          uint64_t output_application_generation) {
  const TsfFocusSnapshot output_domain = {
      .focus_epoch = output_focus_epoch,
      .focus_revision = output_focus_revision,
  };
  if (!IsOutputApplicationCurrent(
          text_service, context, output_domain,
          output_application_generation,
          /*require_nonsecure=*/false)) {
    return S_FALSE;
  }
  const HRESULT result = DoEditSessionInComposition(
      text_service, context, write_cookie, output, output_domain,
      output_application_generation);
  if (!IsOutputApplicationCurrent(
          text_service, context, output_domain,
          output_application_generation,
          /*require_nonsecure=*/false)) {
    return S_FALSE;
  }
  UpdateUI(text_service, context, write_cookie, output_focus_epoch,
           output_focus_revision);
  return result;
}

void TipEditSessionImpl::UpdateUI(TipTextService* text_service,
                                  ITfContext* context,
                                  TfEditCookie read_cookie,
                                  uint64_t output_focus_epoch,
                                  int32_t output_focus_revision) {
  TipUiHandler::Update(text_service, context, read_cookie, output_focus_epoch,
                       output_focus_revision);
}

}  // namespace tsf
}  // namespace win32
}  // namespace mozc
