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

#include "win32/tip/tip_ui_element_manager.h"

#include <msctf.h>
#include <objbase.h>
#include <wil/com.h>

#include <cstdint>
#include <memory>
#include <utility>

#include "absl/container/flat_hash_map.h"
#include "base/win32/com.h"
#include "protocol/candidate_window.pb.h"
#include "protocol/commands.pb.h"
#include "protocol/renderer_command.pb.h"
#include "win32/base/input_state.h"
#include "win32/tip/tip_input_mode_manager.h"
#include "win32/tip/tip_grimodex_context_util.h"
#include "win32/tip/tip_private_context.h"
#include "win32/tip/tip_text_service.h"
#include "win32/tip/tip_thread_context.h"
#include "win32/tip/tip_ui_element_conventional.h"

namespace mozc {
namespace win32 {
namespace tsf {
namespace {

using ::mozc::commands::Output;
using IndicatorInfo = ::mozc::commands::RendererCommand_IndicatorInfo;

bool IsUiDomainCurrent(TipTextService* text_service, ITfContext* context,
                       TsfFocusSnapshot element_domain,
                       uint64_t output_generation) {
  if (!IsTsfContextFocusedForSnapshot(text_service, context, element_domain,
                                      /*require_nonsecure=*/true)) {
    return false;
  }
  const std::shared_ptr<TipPrivateContext> private_context =
      text_service->GetPrivateContext(context);
  return private_context &&
         private_context->IsLastOutputForFocusDomainAndGeneration(
             element_domain.focus_epoch, element_domain.focus_revision,
             output_generation) &&
         IsTsfContextFocusedForSnapshot(text_service, context, element_domain,
                                        /*require_nonsecure=*/true) &&
         private_context->IsLastOutputForFocusDomainAndGeneration(
             element_domain.focus_epoch, element_domain.focus_revision,
             output_generation);
}

HRESULT EndUI(const wil::com_ptr_nothrow<ITfUIElementMgr>& ui_element_manager,
              const wil::com_ptr_nothrow<ITfUIElement>& ui_element,
              DWORD element_id) {
  if (ui_element) {
    ui_element->Show(FALSE);
  }
  if (ui_element_manager && element_id != TF_INVALID_UIELEMENTID) {
    ui_element_manager->EndUIElement(element_id);
  }
  return S_OK;
}

HRESULT BeginUI(const wil::com_ptr_nothrow<ITfUIElementMgr>& ui_element_manager,
                const wil::com_ptr_nothrow<ITfUIElement>& ui_element,
                DWORD* new_element_id, TipTextService* text_service,
                ITfContext* context, TsfFocusSnapshot element_domain,
                uint64_t output_generation) {
  if (!IsUiDomainCurrent(text_service, context, element_domain,
                         output_generation)) {
    return E_ABORT;
  }
  BOOL show = FALSE;
  *new_element_id = TF_INVALID_UIELEMENTID;
  const HRESULT result = ui_element_manager->BeginUIElement(
      ui_element.get(), &show, new_element_id);
  if (FAILED(result)) {
    return result;
  }
  if (!IsUiDomainCurrent(text_service, context, element_domain,
                         output_generation)) {
    EndUI(ui_element_manager, ui_element, *new_element_id);
    *new_element_id = TF_INVALID_UIELEMENTID;
    return E_ABORT;
  }
  ui_element->Show(show);
  if (!IsUiDomainCurrent(text_service, context, element_domain,
                         output_generation)) {
    EndUI(ui_element_manager, ui_element, *new_element_id);
    *new_element_id = TF_INVALID_UIELEMENTID;
    return E_ABORT;
  }
  return S_OK;
}

bool UpdateUI(const wil::com_ptr_nothrow<ITfUIElementMgr>& ui_element_manager,
              DWORD element_id, TipTextService* text_service,
              ITfContext* context, TsfFocusSnapshot element_domain,
              uint64_t output_generation) {
  if (element_id == TF_INVALID_UIELEMENTID ||
      !IsUiDomainCurrent(text_service, context, element_domain,
                         output_generation)) {
    return false;
  }
  const HRESULT result = ui_element_manager->UpdateUIElement(element_id);
  return SUCCEEDED(result) &&
         IsUiDomainCurrent(text_service, context, element_domain,
                           output_generation);
}

}  // namespace

ITfUIElement* TipUiElementManager::GetElement(UIElementFlags element) const {
  const UiElementMap::const_iterator it = ui_element_map_.find(element);
  if (it == ui_element_map_.end()) {
    return nullptr;
  }
  return it->second.element.get();
}

DWORD TipUiElementManager::GetElementId(UIElementFlags element) const {
  const UiElementMap::const_iterator it = ui_element_map_.find(element);
  if (it == ui_element_map_.end()) {
    return TF_INVALID_UIELEMENTID;
  }
  return it->second.id;
}

HRESULT TipUiElementManager::OnUpdate(TipTextService* text_service,
                                      ITfContext* context,
                                      TsfFocusSnapshot element_domain,
                                      uint64_t output_generation) {
  if (!IsTsfContextFocusedForSnapshot(text_service, context, element_domain,
                                      /*require_nonsecure=*/true)) {
    return S_FALSE;
  }
  const std::shared_ptr<TipPrivateContext> private_context =
      text_service->GetPrivateContext(context);
  if (!private_context ||
      !private_context->IsLastOutputForFocusDomain(
          element_domain.focus_epoch, element_domain.focus_revision)) {
    return S_FALSE;
  }
  if (private_context->last_output_generation() != output_generation) {
    return S_FALSE;
  }
  if (!IsUiDomainCurrent(text_service, context, element_domain,
                         output_generation)) {
    return S_FALSE;
  }
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  wil::com_ptr_nothrow<ITfThreadMgr> thread_manager =
      text_service->GetThreadManager();
  if (!thread_context || !thread_manager) {
    return S_FALSE;
  }
  auto ui_element_manager = ComQuery<ITfUIElementMgr>(thread_manager);
  if (!ui_element_manager ||
      text_service->GetThreadContextLease().get() != thread_context.get() ||
      !IsUiDomainCurrent(text_service, context, element_domain,
                         output_generation)) {
    return E_FAIL;
  }

  const Output output = private_context->last_output();

  uint32_t existence_bits = kNoneWindow;
  if (output.has_candidate_window() &&
      output.candidate_window().has_category()) {
    switch (output.candidate_window().category()) {
      case commands::SUGGESTION:
        existence_bits |= kSuggestWindow;
        break;
      case commands::PREDICTION:
      case commands::CONVERSION:
        existence_bits |= kCandidateWindow;
        break;
      default:
        break;
    }
  }
  if (private_context->input_behavior().use_mode_indicator &&
      thread_context->GetInputModeManager()->IsIndicatorVisible()) {
    existence_bits |= kIndicatorWindow;
  }

  DWORD suggest_ui_id = TF_INVALID_UIELEMENTID;
  uint64_t suggest_generation = 0;
  wil::com_ptr_nothrow<ITfUIElement> suggest_ui;
  {
    const UiElementMap::const_iterator it =
        ui_element_map_.find(kSuggestWindow);
    if (it != ui_element_map_.end()) {
      suggest_ui_id = it->second.id;
      suggest_generation = it->second.output_generation;
      suggest_ui = it->second.element;
    }
  }
  DWORD candidate_ui_id = TF_INVALID_UIELEMENTID;
  uint64_t candidate_generation = 0;
  wil::com_ptr_nothrow<ITfUIElement> candidate_ui;
  {
    const UiElementMap::const_iterator it =
        ui_element_map_.find(kCandidateWindow);
    if (it != ui_element_map_.end()) {
      candidate_ui_id = it->second.id;
      candidate_generation = it->second.output_generation;
      candidate_ui = it->second.element;
    }
  }
  DWORD indicator_ui_id = TF_INVALID_UIELEMENTID;
  uint64_t indicator_generation = 0;
  wil::com_ptr_nothrow<ITfUIElement> indicator_ui;
  {
    const UiElementMap::const_iterator it =
        ui_element_map_.find(kIndicatorWindow);
    if (it != ui_element_map_.end()) {
      indicator_ui_id = it->second.id;
      indicator_generation = it->second.output_generation;
      indicator_ui = it->second.element;
    }
  }

  enum UpdateMode {
    kUINone,            // UI is not changed.
    kUIBeginAndUpdate,  // Begin() and Update() should be called.
    kUIEnd,             // End() should be called.
    kUIUpdate,          // Update() should be called.
    kUIReplace,         // End the stale generation, then begin the new one.
  };

  UpdateMode suggest_mode = kUINone;
  if ((existence_bits & kSuggestWindow) == kSuggestWindow) {
    if (suggest_ui_id == TF_INVALID_UIELEMENTID) {
      suggest_mode = kUIBeginAndUpdate;
    } else if (suggest_generation != output_generation) {
      suggest_mode = kUIReplace;
    } else {
      suggest_mode = kUIUpdate;
    }
  } else {
    if (suggest_ui_id != TF_INVALID_UIELEMENTID) {
      suggest_mode = kUIEnd;
    }
  }

  UpdateMode candidate_mode = kUINone;
  if ((existence_bits & kCandidateWindow) == kCandidateWindow) {
    if (candidate_ui_id == TF_INVALID_UIELEMENTID) {
      candidate_mode = kUIBeginAndUpdate;
    } else if (candidate_generation != output_generation) {
      candidate_mode = kUIReplace;
    } else {
      candidate_mode = kUIUpdate;
    }
  } else {
    if (candidate_ui_id != TF_INVALID_UIELEMENTID) {
      candidate_mode = kUIEnd;
    }
  }

  UpdateMode indicator_mode = kUINone;
  if ((existence_bits & kIndicatorWindow) == kIndicatorWindow) {
    if (indicator_ui_id == TF_INVALID_UIELEMENTID) {
      indicator_mode = kUIBeginAndUpdate;
    } else if (indicator_generation != output_generation) {
      indicator_mode = kUIReplace;
    } else {
      indicator_mode = kUIUpdate;
    }
  } else {
    if (indicator_ui_id != TF_INVALID_UIELEMENTID) {
      indicator_mode = kUIEnd;
    }
  }

  if (suggest_mode == kUIEnd || suggest_mode == kUIReplace) {
    const auto it = ui_element_map_.find(kSuggestWindow);
    if (it != ui_element_map_.end() && it->second.id == suggest_ui_id &&
        it->second.element.get() == suggest_ui.get() &&
        it->second.output_generation == suggest_generation) {
      ui_element_map_.erase(it);
      EndUI(ui_element_manager, suggest_ui, suggest_ui_id);
    }
    if (!IsUiDomainCurrent(text_service, context, element_domain,
                           output_generation)) {
      return S_FALSE;
    }
    suggest_ui_id = TF_INVALID_UIELEMENTID;
  }
  if (candidate_mode == kUIEnd || candidate_mode == kUIReplace) {
    const auto it = ui_element_map_.find(kCandidateWindow);
    if (it != ui_element_map_.end() && it->second.id == candidate_ui_id &&
        it->second.element.get() == candidate_ui.get() &&
        it->second.output_generation == candidate_generation) {
      ui_element_map_.erase(it);
      EndUI(ui_element_manager, candidate_ui, candidate_ui_id);
    }
    if (!IsUiDomainCurrent(text_service, context, element_domain,
                           output_generation)) {
      return S_FALSE;
    }
    candidate_ui_id = TF_INVALID_UIELEMENTID;
  }
  if (indicator_mode == kUIEnd || indicator_mode == kUIReplace) {
    const auto it = ui_element_map_.find(kIndicatorWindow);
    if (it != ui_element_map_.end() && it->second.id == indicator_ui_id &&
        it->second.element.get() == indicator_ui.get() &&
        it->second.output_generation == indicator_generation) {
      ui_element_map_.erase(it);
      EndUI(ui_element_manager, indicator_ui, indicator_ui_id);
    }
    if (!IsUiDomainCurrent(text_service, context, element_domain,
                           output_generation)) {
      return S_FALSE;
    }
    indicator_ui_id = TF_INVALID_UIELEMENTID;
  }

  if (suggest_mode == kUIBeginAndUpdate || suggest_mode == kUIReplace) {
    wil::com_ptr_nothrow<ITfUIElement> suggest_ui =
        TipUiElementConventional::New(
            TipUiElementConventional::kUnobservableSuggestWindow, text_service,
            context, element_domain, output_generation);
    if (suggest_ui) {
      DWORD new_suggest_ui_id = TF_INVALID_UIELEMENTID;
      if (SUCCEEDED(BeginUI(ui_element_manager, suggest_ui,
                            &new_suggest_ui_id, text_service, context,
                            element_domain, output_generation)) &&
          !ui_element_map_.contains(kSuggestWindow)) {
        ui_element_map_[kSuggestWindow].element = std::move(suggest_ui);
        ui_element_map_[kSuggestWindow].id = new_suggest_ui_id;
        ui_element_map_[kSuggestWindow].output_generation = output_generation;
        suggest_ui_id = new_suggest_ui_id;
      } else if (new_suggest_ui_id != TF_INVALID_UIELEMENTID) {
        EndUI(ui_element_manager, suggest_ui, new_suggest_ui_id);
      }
    }
  }
  if (candidate_mode == kUIBeginAndUpdate || candidate_mode == kUIReplace) {
    wil::com_ptr_nothrow<ITfUIElement> candidate_ui =
        TipUiElementConventional::New(
            TipUiElementConventional::kCandidateWindow, text_service, context,
            element_domain, output_generation);
    if (candidate_ui) {
      DWORD new_candidate_ui_id = TF_INVALID_UIELEMENTID;
      if (SUCCEEDED(BeginUI(ui_element_manager, candidate_ui,
                            &new_candidate_ui_id, text_service, context,
                            element_domain, output_generation)) &&
          !ui_element_map_.contains(kCandidateWindow)) {
        ui_element_map_[kCandidateWindow].element = std::move(candidate_ui);
        ui_element_map_[kCandidateWindow].id = new_candidate_ui_id;
        ui_element_map_[kCandidateWindow].output_generation =
            output_generation;
        candidate_ui_id = new_candidate_ui_id;
      } else if (new_candidate_ui_id != TF_INVALID_UIELEMENTID) {
        EndUI(ui_element_manager, candidate_ui, new_candidate_ui_id);
      }
    }
  }
  if (indicator_mode == kUIBeginAndUpdate || indicator_mode == kUIReplace) {
    wil::com_ptr_nothrow<ITfUIElement> indicator_ui =
        TipUiElementConventional::New(
            TipUiElementConventional::KIndicatorWindow, text_service, context,
            element_domain, output_generation);
    if (indicator_ui) {
      DWORD new_indicator_ui_id = TF_INVALID_UIELEMENTID;
      if (SUCCEEDED(BeginUI(ui_element_manager, indicator_ui,
                            &new_indicator_ui_id, text_service, context,
                            element_domain, output_generation)) &&
          !ui_element_map_.contains(kIndicatorWindow)) {
        ui_element_map_[kIndicatorWindow].element = std::move(indicator_ui);
        ui_element_map_[kIndicatorWindow].id = new_indicator_ui_id;
        ui_element_map_[kIndicatorWindow].output_generation =
            output_generation;
        indicator_ui_id = new_indicator_ui_id;
      } else if (new_indicator_ui_id != TF_INVALID_UIELEMENTID) {
        EndUI(ui_element_manager, indicator_ui, new_indicator_ui_id);
      }
    }
  }

  if (suggest_mode == kUIUpdate || suggest_mode == kUIBeginAndUpdate ||
      suggest_mode == kUIReplace) {
    if (!UpdateUI(ui_element_manager, suggest_ui_id, text_service, context,
                  element_domain, output_generation)) {
      return S_FALSE;
    }
  }
  if (candidate_mode == kUIUpdate || candidate_mode == kUIBeginAndUpdate ||
      candidate_mode == kUIReplace) {
    if (!UpdateUI(ui_element_manager, candidate_ui_id, text_service, context,
                  element_domain, output_generation)) {
      return S_FALSE;
    }
  }
  if (indicator_mode == kUIUpdate || indicator_mode == kUIBeginAndUpdate ||
      indicator_mode == kUIReplace) {
    if (!UpdateUI(ui_element_manager, indicator_ui_id, text_service, context,
                  element_domain, output_generation)) {
      return S_FALSE;
    }
  }
  return S_OK;
}

void TipUiElementManager::EndAll(TipTextService* text_service) {
  UiElementMap elements;
  elements.swap(ui_element_map_);

  wil::com_ptr_nothrow<ITfUIElementMgr> ui_element_manager;
  if (text_service != nullptr) {
    wil::com_ptr_nothrow<ITfThreadMgr> thread_manager =
        text_service->GetThreadManager();
    if (thread_manager) {
      ui_element_manager = ComQuery<ITfUIElementMgr>(thread_manager);
    }
  }
  for (const auto& entry : elements) {
    EndUI(ui_element_manager, entry.second.element, entry.second.id);
  }
}

bool TipUiElementManager::IsVisible(ITfUIElementMgr* ui_element_manager,
                                    UIElementFlags element) const {
  if (ui_element_manager == nullptr) {
    return false;
  }
  const UiElementMap::const_iterator it = ui_element_map_.find(element);
  if (it == ui_element_map_.end()) {
    return false;
  }
  wil::com_ptr_nothrow<ITfUIElement> element = it->second.element;
  if (!element) {
    return false;
  }

  BOOL shown = FALSE;
  if (FAILED(element->IsShown(&shown))) {
    return false;
  }
  return !!shown;
}

}  // namespace tsf
}  // namespace win32
}  // namespace mozc
