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

#include "win32/tip/tip_ui_handler.h"

#include <msctf.h>
#include <wil/com.h>

#include <cstdint>
#include <memory>

#include "protocol/commands.pb.h"
#include "win32/tip/tip_input_mode_manager.h"
#include "win32/tip/tip_grimodex_context_util.h"
#include "win32/tip/tip_status.h"
#include "win32/tip/tip_text_service.h"
#include "win32/tip/tip_thread_context.h"
#include "win32/tip/tip_ui_handler_conventional.h"

namespace mozc {
namespace win32 {
namespace tsf {
namespace {

using ::mozc::commands::CompositionMode;

void UpdateLanguageBarOnFocusChange(TipTextService* text_service,
                                    ITfDocumentMgr* document_manager,
                                    TsfFocusSnapshot focus_domain);

void ResyncLanguageBarForCurrentFocus(TipTextService* text_service) {
  if (text_service == nullptr) {
    return;
  }
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  wil::com_ptr_nothrow<ITfThreadMgr> thread_manager =
      text_service->GetThreadManager();
  if (!thread_context || !thread_manager) {
    return;
  }
  const TsfFocusSnapshot focus_domain =
      CaptureTsfFocusSnapshot(thread_context.get());
  if (!text_service->HasThreadFocus()) {
    UpdateLanguageBarOnFocusChange(text_service, nullptr, focus_domain);
    return;
  }
  wil::com_ptr_nothrow<ITfDocumentMgr> document_manager;
  if (FAILED(thread_manager->GetFocus(&document_manager)) ||
      text_service->GetThreadContextLease().get() != thread_context.get() ||
      !IsTsfFocusSnapshotCurrent(thread_context.get(), focus_domain)) {
    return;
  }
  if (!text_service->HasThreadFocus()) {
    const std::shared_ptr<TipThreadContext> current =
        text_service->GetThreadContextLease();
    if (current) {
      UpdateLanguageBarOnFocusChange(
          text_service, nullptr, CaptureTsfFocusSnapshot(current.get()));
    }
    return;
  }
  UpdateLanguageBarOnFocusChange(text_service, document_manager.get(),
                                 focus_domain);
}

void UpdateLanguageBarOnFocusChange(TipTextService* text_service,
                                    ITfDocumentMgr* document_manager,
                                    TsfFocusSnapshot focus_domain) {
  if (!text_service) {
    return;
  }

  const auto is_focus_event_current = [&]() {
    const std::shared_ptr<TipThreadContext> current =
        text_service->GetThreadContextLease();
    return current &&
           IsTsfFocusSnapshotCurrent(current.get(), focus_domain);
  };
  if (!is_focus_event_current() || !text_service->IsLangbarInitialized()) {
    // If language bar is not initialized, there is nothing to do here.
    return;
  }

  HRESULT result = S_OK;
  wil::com_ptr_nothrow<ITfThreadMgr> thread_manager =
      text_service->GetThreadManager();
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_manager || !thread_context) {
    return;
  }

  bool disabled = false;
  {
    if (document_manager == nullptr) {
      // When |document_manager| is null, we should show "disabled" icon
      // as if |ImmAssociateContext(window_handle, nullptr)| was called.
      disabled = true;
    } else {
      wil::com_ptr_nothrow<ITfContext> context;
      result = document_manager->GetTop(&context);
      if (!is_focus_event_current()) {
        return;
      }
      if (SUCCEEDED(result)) {
        disabled = TipStatus::IsDisabledContext(context.get());
        if (!is_focus_event_current()) {
          return;
        }
      }
    }
  }

  if (text_service->GetThreadContextLease().get() != thread_context.get() ||
      !is_focus_event_current()) {
    return;
  }

  const TipInputModeManager* input_mode_manager =
      thread_context->GetInputModeManager();
  const bool open = input_mode_manager->GetEffectiveOpenClose();
  const CompositionMode mozc_mode =
      open ? static_cast<CompositionMode>(
                 input_mode_manager->GetEffectiveConversionMode())
           : commands::DIRECT;
  if (text_service->GetThreadContextLease().get() != thread_context.get() ||
      !is_focus_event_current()) {
    return;
  }
  text_service->UpdateLangbar(!disabled, static_cast<uint32_t>(mozc_mode));
  if (!is_focus_event_current()) {
    ResyncLanguageBarForCurrentFocus(text_service);
    return;
  }
  const bool current_open = input_mode_manager->GetEffectiveOpenClose();
  const CompositionMode current_mode =
      current_open
          ? static_cast<CompositionMode>(
                input_mode_manager->GetEffectiveConversionMode())
          : commands::DIRECT;
  if (current_open != open || current_mode != mozc_mode) {
    ResyncLanguageBarForCurrentFocus(text_service);
  }
}

bool UpdateInternal(TipTextService* text_service, ITfContext* context,
                    TfEditCookie read_cookie, uint64_t output_focus_epoch,
                    int32_t output_focus_revision) {
  return TipUiHandlerConventional::Update(
      text_service, context, read_cookie, output_focus_epoch,
      output_focus_revision);
}

}  // namespace

void TipUiHandler::OnActivate(TipTextService* text_service) {
  TipUiHandlerConventional::OnActivate(text_service);
}

void TipUiHandler::OnDeactivate(TipTextService* text_service) {
  TipUiHandlerConventional::OnDeactivate();
}

void TipUiHandler::OnDocumentMgrChanged(TipTextService* text_service,
                                        ITfDocumentMgr* document_manager,
                                        uint64_t focus_epoch,
                                        int32_t focus_revision) {
  UpdateLanguageBarOnFocusChange(
      text_service, document_manager,
      {.focus_epoch = focus_epoch, .focus_revision = focus_revision});
}

void TipUiHandler::OnFocusChange(TipTextService* text_service,
                                 ITfDocumentMgr* focused_document_manager,
                                 uint64_t focus_epoch,
                                 int32_t focus_revision) {
  const TsfFocusSnapshot focus_domain = {
      .focus_epoch = focus_epoch,
      .focus_revision = focus_revision,
  };
  const auto is_focus_event_current = [&]() {
    const std::shared_ptr<TipThreadContext> current =
        text_service->GetThreadContextLease();
    return current && IsTsfFocusSnapshotCurrent(current.get(), focus_domain);
  };
  if (!is_focus_event_current()) {
    return;
  }
  text_service->EndAllUiElements();
  if (!is_focus_event_current()) {
    return;
  }
  TipUiHandlerConventional::OnFocusChange(text_service,
                                          focused_document_manager,
                                          focus_epoch, focus_revision);
  if (!is_focus_event_current()) {
    return;
  }
  UpdateLanguageBarOnFocusChange(text_service, focused_document_manager,
                                 focus_domain);
}

bool TipUiHandler::Update(TipTextService* text_service, ITfContext* context,
                          TfEditCookie read_cookie,
                          uint64_t output_focus_epoch,
                          int32_t output_focus_revision) {
  const TsfFocusSnapshot output_domain = {
      .focus_epoch = output_focus_epoch,
      .focus_revision = output_focus_revision,
  };
  if (!IsTsfContextFocusedForSnapshot(text_service, context, output_domain,
                                      /*require_nonsecure=*/false)) {
    return false;
  }
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_context) {
    return false;
  }
  const bool result = UpdateInternal(text_service, context, read_cookie,
                                     output_focus_epoch,
                                     output_focus_revision);
  if (!IsTsfContextFocusedForSnapshot(text_service, context, output_domain,
                                      /*require_nonsecure=*/false)) {
    return false;
  }
  const TipInputModeManager* input_mode_manager =
      thread_context->GetInputModeManager();
  const bool open = input_mode_manager->GetEffectiveOpenClose();
  const CompositionMode mozc_mode = static_cast<CompositionMode>(
      input_mode_manager->GetEffectiveConversionMode());
  if (open) {
    text_service->UpdateLangbar(true, mozc_mode);
  } else {
    text_service->UpdateLangbar(true, commands::DIRECT);
  }
  if (!IsTsfContextFocusedForSnapshot(text_service, context, output_domain,
                                      /*require_nonsecure=*/false)) {
    ResyncLanguageBarForCurrentFocus(text_service);
    return false;
  }
  const bool current_open = input_mode_manager->GetEffectiveOpenClose();
  const CompositionMode current_mode = static_cast<CompositionMode>(
      input_mode_manager->GetEffectiveConversionMode());
  if (current_open != open || (current_open && current_mode != mozc_mode)) {
    ResyncLanguageBarForCurrentFocus(text_service);
  }
  return result;
}

bool TipUiHandler::OnDllProcessAttach(HINSTANCE module_handle,
                                      bool static_loading) {
  TipUiHandlerConventional::OnDllProcessAttach(module_handle, static_loading);
  return true;
}

void TipUiHandler::OnDllProcessDetach(HINSTANCE module_handle,
                                      bool process_shutdown) {
  TipUiHandlerConventional::OnDllProcessDetach(module_handle, process_shutdown);
}

}  // namespace tsf
}  // namespace win32
}  // namespace mozc
