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

#include "win32/tip/tip_keyevent_handler.h"

#include <msctf.h>
#include <windows.h>

#include <cstdint>
#include <memory>

#include "absl/log/check.h"
#include "absl/log/log.h"
#include "client/client_interface.h"
#include "protocol/commands.pb.h"
#include "session/key_info_util.h"
#include "win32/base/conversion_mode_util.h"
#include "win32/base/deleter.h"
#include "win32/base/input_state.h"
#include "win32/base/keyboard.h"
#include "win32/base/keyevent_handler.h"
#include "win32/base/surrogate_pair_observer.h"
#include "win32/tip/tip_edit_session.h"
#include "win32/tip/tip_grimodex_context_util.h"
#include "win32/tip/tip_input_mode_manager.h"
#include "win32/tip/tip_private_context.h"
#include "win32/tip/tip_status.h"
#include "win32/tip/tip_text_service.h"
#include "win32/tip/tip_thread_context.h"

namespace mozc {
namespace win32 {
namespace tsf {
namespace {

using ::mozc::commands::CompositionMode;
using ::mozc::commands::Context;
using ::mozc::commands::SessionCommand;

// Defined in the following white paper.
// http://msdn.microsoft.com/en-us/library/windows/apps/hh967425.aspx
constexpr UINT kTouchKeyboardNextPage = 0xf003;
constexpr UINT kTouchKeyboardPreviousPage = 0xf004;

// Unlike IMM32 Mozc which is marked as IME_PROP_ACCEPT_WIDE_VKEY,
// TSF Mozc cannot always receive a VK_PACKET keyevent whose high word consists
// of a Unicode character. To retrieve the underlaying Unicode character,
// use ToUnicode API as documented in the following white paper.
// http://msdn.microsoft.com/en-us/library/windows/apps/hh967425.aspx
VirtualKey GetVK(WPARAM wparam, const KeyboardStatus& keyboad_status) {
  if (LOWORD(wparam) != VK_PACKET) {
    return VirtualKey::FromVirtualKey(wparam);
  }

  const UINT scan_code = ::MapVirtualKey(wparam, MAPVK_VK_TO_VSC);
  wchar_t buffer[4] = {};
  if (::ToUnicode(wparam, scan_code, keyboad_status.status(), buffer,
                  std::size(buffer), 0) != 1) {
    return VirtualKey::FromVirtualKey(wparam);
  }
  const uint32_t ucs2 = buffer[0];
  if (ucs2 == L' ') {
    return VirtualKey::FromVirtualKey(VK_SPACE);
  }
  if (L'0' <= ucs2 && ucs2 <= L'9') {
    return VirtualKey::FromVirtualKey(ucs2);
  }
  if (L'a' <= ucs2 && ucs2 <= L'z') {
    return VirtualKey::FromVirtualKey(L'z' - ucs2 + L'A');
  }
  if (L'A' <= ucs2 && ucs2 <= L'Z') {
    return VirtualKey::FromVirtualKey(ucs2);
  }

  // Emulate IME_PROP_ACCEPT_WIDE_VKEY.
  return VirtualKey::FromCombinedVirtualKey(ucs2 << 16 | VK_PACKET);
}

void ShowModeIndicatorAndUpdateUI(TipTextService* text_service,
                                  TipInputModeManager* input_mode_manager) {
  const TipInputModeManager::Action show_action =
      input_mode_manager->ShowModeIndicator();
  if (show_action == TipInputModeManager::Action::kUpdateUI) {
    text_service->PostUIUpdateMessage();
  }
}

bool IsNoOpModeIndicatorKey(const InputBehavior& behavior,
                            const InputState& current_state,
                            const KeyEventHandlerResult& result) {
  if (!result.has_key_information) {
    return false;
  }

  if (current_state.open) {
    return KeyInfoUtil::ContainsKeyInformation(
        behavior.active_mode_ime_on_keys, result.key_information);
  }

  return KeyInfoUtil::ContainsKeyInformation(
      behavior.direct_mode_ime_off_keys, result.key_information);
}

bool UpdateNoOpModeIndicatorKeyState(
    TipTextService* text_service, TipPrivateContext* private_context,
    TipInputModeManager* input_mode_manager, bool is_key_down,
    const InputBehavior& behavior, const InputState& current_state,
    const KeyEventHandlerResult& result, bool is_on_key) {
  if (!result.has_key_information) {
    if (is_key_down) {
      private_context->ClearPendingModeIndicatorKey();
    }
    return false;
  }

  if (is_key_down) {
    if (IsNoOpModeIndicatorKey(behavior, current_state, result)) {
      private_context->SetPendingModeIndicatorKey(result.key_information);
    } else {
      private_context->ClearPendingModeIndicatorKey();
    }
    return false;
  }

  if (!private_context->IsPendingModeIndicatorKey(result.key_information)) {
    return false;
  }

  if (!is_on_key) {
    ShowModeIndicatorAndUpdateUI(text_service, input_mode_manager);
    private_context->MarkPendingModeIndicatorShownOnTestKey();
    return true;
  }

  if (!private_context->IsPendingModeIndicatorShownOnTestKey()) {
    ShowModeIndicatorAndUpdateUI(text_service, input_mode_manager);
  }

  private_context->ClearPendingModeIndicatorKey();
  return true;
}

bool GetOpenAndMode(TipTextService* text_service, ITfContext* context,
                    bool* open, uint32_t* logical_mode,
                    uint32_t* visible_mode) {
  DCHECK(text_service);
  DCHECK(context);
  DCHECK(open);
  DCHECK(logical_mode);
  DCHECK(visible_mode);
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_context) {
    return false;
  }
  const TipInputModeManager* input_mode_manager =
      thread_context->GetInputModeManager();
  const bool is_open = input_mode_manager->GetEffectiveOpenClose();
  *open = (!TipStatus::IsDisabledContext(context) && is_open);
  const std::shared_ptr<TipThreadContext> current_thread_context =
      text_service->GetThreadContextLease();
  if (!current_thread_context ||
      current_thread_context.get() != thread_context.get()) {
    return false;
  }

  bool prefer_kana_input = false;
  const std::shared_ptr<TipPrivateContext> private_context =
      text_service->GetPrivateContext(context);
  if (private_context) {
    prefer_kana_input = private_context->input_behavior().prefer_kana_input;
  }
  const CompositionMode tsf_mode =
      static_cast<CompositionMode>(input_mode_manager->GetTsfConversionMode());
  const CompositionMode effective_mode = static_cast<CompositionMode>(
      input_mode_manager->GetEffectiveConversionMode());

  const bool has_valid_logical_mode = ConversionModeUtil::ToNativeMode(
      tsf_mode, prefer_kana_input, logical_mode);
  const bool has_valid_visible_mode = ConversionModeUtil::ToNativeMode(
      effective_mode, prefer_kana_input, visible_mode);
  return has_valid_logical_mode && has_valid_visible_mode;
}

void FillMozcContextCommon(TipTextService* text_service, ITfContext* context,
                           bool include_surrounding_text,
                           Context* mozc_context) {
  if (mozc_context == nullptr) {
    return;
  }

  *mozc_context = BuildTsfMozcContext(text_service, context,
                                      include_surrounding_text);
}

HRESULT OnTestKey(TipTextService* text_service, ITfContext* context,
                  bool is_key_down, WPARAM wparam, LPARAM lparam, BOOL* eaten) {
  DCHECK(text_service);
  DCHECK(eaten);
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_context) {
    *eaten = TRUE;
    return S_OK;
  }
  const TsfFocusSnapshot test_domain =
      CaptureTsfFocusSnapshot(thread_context.get());
  if (!IsTsfContextFocusedForSnapshot(text_service, context, test_domain,
                                      /*require_nonsecure=*/false)) {
    *eaten = TRUE;
    return S_OK;
  }
  const std::shared_ptr<TipPrivateContext> private_context =
      text_service->GetPrivateContext(context);
  if (private_context == nullptr) {
    *eaten = FALSE;
    return S_OK;
  }
  const uint64_t key_transaction_generation =
      private_context->ReserveKeyTransactionForFocusDomain(
          test_domain.focus_epoch, test_domain.focus_revision);
  const auto is_test_transaction_current = [&]() {
    return IsTsfContextFocusedForSnapshot(text_service, context, test_domain,
                                          /*require_nonsecure=*/false) &&
           private_context->IsKeyTransactionForFocusDomain(
               test_domain.focus_epoch, test_domain.focus_revision,
               key_transaction_generation);
  };
  if (!is_test_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }

  BYTE key_state[256] = {};
  if (!::GetKeyboardState(key_state)) {
    *eaten = is_test_transaction_current() ? FALSE : TRUE;
    return S_OK;
  }
  if (!is_test_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }

  bool open = false;
  uint32_t logical_mode = 0;
  uint32_t visible_mode = 0;
  if (!GetOpenAndMode(text_service, context, &open, &logical_mode,
                      &visible_mode)) {
    *eaten = is_test_transaction_current() ? FALSE : TRUE;
    return S_OK;
  }
  if (!is_test_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }

  const KeyboardStatus keyboard_status(key_state);
  const LParamKeyInfo key_info(lparam);
  VirtualKey vk = GetVK(wparam, keyboard_status);

  if (open) {
    // Check if this key event is handled by VKBackBasedDeleter to support
    // *deletion_range* rule.
    const ClientAction vk_back_action =
        private_context->GetDeleter()->OnKeyEvent(
            vk.virtual_key(), key_info.IsKeyDownInImeProcessKey(), true);
    if (!is_test_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    switch (vk_back_action) {
      case ClientAction::DO_DEFAULT_ACTION:
        // do nothing.
        break;
      case ClientAction::CALL_END_DELETION_THEN_DO_DEFAULT_ACTION:
        private_context->GetDeleter()->EndDeletion();
        private_context->ClearPendingOutputFocusDomain();
        break;
      case ClientAction::SEND_KEY_TO_APPLICATION:
        *eaten = FALSE;  // Do not consume this key.
        return S_OK;
      case ClientAction::CONSUME_KEY_BUT_NEVER_SEND_TO_SERVER:
        *eaten = TRUE;  // Consume this key but do not send this key to server.
        return S_OK;
      case ClientAction::CALL_END_DELETION_BUT_NEVER_SEND_TO_SERVER:
      case ClientAction::APPLY_PENDING_STATUS:
      default:
        DLOG(FATAL) << "this action is not applicable to OnTestKey.";
        *eaten = FALSE;
        return E_UNEXPECTED;
    }

    const SurrogatePairObserver::ClientAction surrogate_action =
        private_context->GetSurrogatePairObserver()->OnTestKeyEvent(
            vk, is_key_down);
    if (!is_test_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    switch (surrogate_action.type) {
      case SurrogatePairObserver::DO_DEFAULT_ACTION:
        break;
      case SurrogatePairObserver::DO_DEFAULT_ACTION_WITH_RETURNED_UCS4:
        vk = VirtualKey::FromUnicode(surrogate_action.codepoint);
        break;
      case SurrogatePairObserver::CONSUME_KEY_BUT_NEVER_SEND_TO_SERVER:
        *eaten = TRUE;
        return S_OK;  // Consume this key but do not send this key to server.
      default:
        DLOG(FATAL) << "this action is not applicable to OnTestKey.";
        break;
    }

    // Handle NextPage/PrevPage button on the on-screen keyboard.
    if (key_info.IsKeyDownInImeProcessKey() &&
        ((vk.wide_char() == kTouchKeyboardNextPage) ||
         (vk.wide_char() == kTouchKeyboardPreviousPage))) {
      *eaten = TRUE;
      return S_OK;
    }
  }

  // Make an immutable snapshot of |private_context->ime_behavior_|, which
  // cannot be substituted for by const reference.
  InputBehavior behavior = private_context->input_behavior();
  Context mozc_context;
  FillMozcContextCommon(text_service, context,
                        /*include_surrounding_text=*/false, &mozc_context);
  if (!is_test_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }

  // Update On/Off mode and conversion mode.
  InputState input_state;
  input_state.last_down_key = private_context->last_down_key();
  input_state.logical_conversion_mode = logical_mode;
  input_state.visible_conversion_mode = visible_mode;
  input_state.open = open;

  InputState next_state;
  commands::Output temporal_output;
  std::unique_ptr<Win32KeyboardInterface> keyboard(
      Win32KeyboardInterface::CreateDefault());

  const KeyEventHandlerResult result = KeyEventHandler::ImeProcessKey(
      vk, key_info.GetScanCodeForMapVirtualKey(), is_key_down, keyboard_status,
      behavior, input_state, mozc_context, private_context->GetClient(),
      keyboard.get(), &next_state, &temporal_output);
  if (!is_test_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }
  if (!result.succeeded) {
    *eaten = FALSE;
    return S_OK;
  }

  if (!is_test_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }
  *private_context->mutable_last_down_key() = next_state.last_down_key;

  if (result.should_be_sent_to_server && temporal_output.has_consumed()) {
    if (!is_test_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    private_context->SetLastOutputForFocusDomain(
        temporal_output, test_domain.focus_epoch, test_domain.focus_revision);
  }

  TipInputModeManager* input_mode_manager =
      thread_context->GetInputModeManager();
  if (!is_test_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }
  const TipInputModeManager::Action action =
      input_mode_manager->OnTestKey(vk, is_key_down, result.should_be_eaten);
  if (!is_test_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }
  if (action == TipInputModeManager::kUpdateUI) {
    text_service->PostUIUpdateMessage();
  }

  if (!is_test_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }
  if (UpdateNoOpModeIndicatorKeyState(
          text_service, private_context.get(), input_mode_manager, is_key_down,
          behavior, input_state, result,
          /*is_on_key=*/false)) {
    if (!is_test_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    *eaten = TRUE;
    return S_OK;
  }

  if (!is_test_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }
  *eaten = result.should_be_eaten ? TRUE : FALSE;
  return S_OK;
}

void FillMozcContextForOnKey(TipTextService* text_service, ITfContext* context,
                             Context* mozc_context) {
  FillMozcContextCommon(text_service, context,
                        /*include_surrounding_text=*/true, mozc_context);
}

HRESULT OnKey(TipTextService* text_service, ITfContext* context,
              bool is_key_down, WPARAM wparam, LPARAM lparam, BOOL* eaten) {
  DCHECK(text_service);
  DCHECK(eaten);
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_context) {
    *eaten = TRUE;
    return S_OK;
  }
  TsfFocusSnapshot output_domain =
      CaptureTsfFocusSnapshot(thread_context.get());
  const std::shared_ptr<TipPrivateContext> private_context =
      text_service->GetPrivateContext(context);
  if (private_context == nullptr) {
    *eaten = FALSE;
    return S_OK;
  }
  if (!IsTsfContextFocusedForSnapshot(text_service, context, output_domain,
                                      /*require_nonsecure=*/false)) {
    *eaten = TRUE;
    return S_OK;
  }
  uint64_t output_application_generation = 0;
  if (private_context->GetDeleter()->IsDeletionOngoing() &&
      private_context->pending_output_application_generation() != 0) {
    output_domain = {
        .focus_epoch = private_context->pending_output_focus_epoch(),
        .focus_revision = private_context->pending_output_focus_revision(),
    };
    output_application_generation =
        private_context->pending_output_application_generation();
  } else {
    output_application_generation =
        private_context->ReserveOutputApplicationForFocusDomain(
            output_domain.focus_epoch, output_domain.focus_revision);
  }
  const auto is_output_application_current = [&]() {
    return private_context->IsOutputApplicationForFocusDomain(
        output_domain.focus_epoch, output_domain.focus_revision,
        output_application_generation);
  };
  if (!IsTsfContextFocusedForSnapshot(text_service, context, output_domain,
                                      /*require_nonsecure=*/false) ||
      !is_output_application_current()) {
    *eaten = TRUE;
    return S_OK;
  }
  uint64_t key_transaction_generation =
      private_context->ReserveKeyTransactionForFocusDomain(
          output_domain.focus_epoch, output_domain.focus_revision);
  const auto is_key_transaction_current = [&]() {
    return private_context->IsKeyTransactionForFocusDomain(
        output_domain.focus_epoch, output_domain.focus_revision,
        key_transaction_generation);
  };
  const auto is_key_output_transaction_current = [&]() {
    return IsTsfContextFocusedForSnapshot(text_service, context, output_domain,
                                          /*require_nonsecure=*/false) &&
           is_output_application_current() && is_key_transaction_current();
  };
  if (!is_key_output_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }
  BYTE key_state[256] = {};
  if (!::GetKeyboardState(key_state)) {
    *eaten = is_key_output_transaction_current() ? FALSE : TRUE;
    return S_OK;
  }
  if (!is_key_output_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }

  bool open = false;
  uint32_t logical_mode = 0;
  uint32_t visible_mode = 0;
  if (!GetOpenAndMode(text_service, context, &open, &logical_mode,
                      &visible_mode)) {
    *eaten = is_key_output_transaction_current() ? FALSE : TRUE;
    return S_OK;
  }
  if (!is_key_output_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }

  const LParamKeyInfo key_info(lparam);
  const KeyboardStatus keyboard_status(key_state);
  VirtualKey vk = GetVK(wparam, keyboard_status);

  const ClientAction vk_back_action = private_context->GetDeleter()->OnKeyEvent(
      vk.virtual_key(), is_key_down, false);
  if (!is_key_output_transaction_current()) {
    *eaten = TRUE;
    return S_OK;
  }

  // Check if this key event is handled by VKBackBasedDeleter to support
  // *deletion_range* rule.
  bool use_pending_output = false;
  bool ignore_this_keyevent = false;
  if (open) {
    switch (vk_back_action) {
      case ClientAction::DO_DEFAULT_ACTION:
        // do nothing.
        break;
      case ClientAction::CALL_END_DELETION_THEN_DO_DEFAULT_ACTION:
        private_context->GetDeleter()->EndDeletion();
        private_context->ClearPendingOutputFocusDomain();
        output_domain = CaptureTsfFocusSnapshot(thread_context.get());
        output_application_generation =
            private_context->ReserveOutputApplicationForFocusDomain(
                output_domain.focus_epoch, output_domain.focus_revision);
        key_transaction_generation =
            private_context->ReserveKeyTransactionForFocusDomain(
                output_domain.focus_epoch, output_domain.focus_revision);
        break;
      case ClientAction::APPLY_PENDING_STATUS:
        use_pending_output = true;
        break;
      case ClientAction::CONSUME_KEY_BUT_NEVER_SEND_TO_SERVER:
        ignore_this_keyevent = true;
        break;
      case ClientAction::CALL_END_DELETION_BUT_NEVER_SEND_TO_SERVER:
        ignore_this_keyevent = true;
        private_context->GetDeleter()->EndDeletion();
        private_context->ClearPendingOutputFocusDomain();
        break;
      case ClientAction::SEND_KEY_TO_APPLICATION:
      default:
        DLOG(FATAL) << "this action is not applicable to OnKey.";
        break;
    }
    if (ignore_this_keyevent) {
      *eaten = TRUE;
      return S_OK;
    }

    const SurrogatePairObserver::ClientAction surrogate_action =
        private_context->GetSurrogatePairObserver()->OnKeyEvent(vk,
                                                                is_key_down);
    if (!is_key_output_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    switch (surrogate_action.type) {
      case SurrogatePairObserver::DO_DEFAULT_ACTION:
        break;
      case SurrogatePairObserver::DO_DEFAULT_ACTION_WITH_RETURNED_UCS4:
        vk = VirtualKey::FromUnicode(surrogate_action.codepoint);
        break;
      case SurrogatePairObserver::CONSUME_KEY_BUT_NEVER_SEND_TO_SERVER:
        ignore_this_keyevent = true;
        break;
      default:
        DLOG(FATAL) << "this action is not applicable to OnKey.";
        ignore_this_keyevent = true;
        break;
    }

    if (ignore_this_keyevent) {
      *eaten = TRUE;
      return S_OK;
    }
  }

  commands::Output temporal_output;
  if (use_pending_output) {
    // In this case, we have a pending output. So no need to call
    // KeyEventHandler::ImeToAsciiEx.
    output_domain = {
        .focus_epoch = private_context->pending_output_focus_epoch(),
        .focus_revision = private_context->pending_output_focus_revision(),
    };
    temporal_output = private_context->GetDeleter()->pending_output();
  } else if (open && is_key_down &&
             (vk.wide_char() == kTouchKeyboardPreviousPage)) {
    // Handle PrevPage button on the on-screen keyboard.
    SessionCommand command;
    command.set_type(SessionCommand::CONVERT_PREV_PAGE);
    if (!SendTsfCommand(text_service, context, private_context->GetClient(),
                        command, &temporal_output, output_domain,
                        /*require_nonsecure=*/false,
                        &output_application_generation)) {
      if (!is_key_output_transaction_current()) {
        *eaten = TRUE;
        return S_OK;
      }
      *eaten = FALSE;
      return E_FAIL;
    }
    if (!is_key_output_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    ignore_this_keyevent = false;
  } else if (open && is_key_down &&
             (vk.wide_char() == kTouchKeyboardNextPage)) {
    // Handle NextPage button on the on-screen keyboard.
    SessionCommand command;
    command.set_type(SessionCommand::CONVERT_NEXT_PAGE);
    if (!SendTsfCommand(text_service, context, private_context->GetClient(),
                        command, &temporal_output, output_domain,
                        /*require_nonsecure=*/false,
                        &output_application_generation)) {
      if (!is_key_output_transaction_current()) {
        *eaten = TRUE;
        return S_OK;
      }
      *eaten = FALSE;
      return E_FAIL;
    }
    if (!is_key_output_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    ignore_this_keyevent = false;
  } else {
    InputBehavior behavior = private_context->input_behavior();

    // Update On/Off state a conversion mode.
    InputState ime_state;
    ime_state.logical_conversion_mode = logical_mode;
    ime_state.visible_conversion_mode = visible_mode;
    ime_state.open = open;
    ime_state.last_down_key = private_context->last_down_key();

    // This call is placed in OnKey instead on OnTestKey because VK_DBE_ROMAN
    // and VK_DBE_NOROMAN are handled as preserved keys in TSF Mozc.
    // See b/3118905 for why this is necessary.
    if (!is_key_output_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    KeyEventHandler::UpdateBehaviorInImeProcessKey(
        vk, is_key_down, ime_state, private_context->mutable_input_behavior());

    std::unique_ptr<Win32KeyboardInterface> keyboard(
        Win32KeyboardInterface::CreateDefault());

    Context mozc_context;
    FillMozcContextForOnKey(text_service, context, &mozc_context);
    if (!is_key_output_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }

    InputState next_state;
    const KeyEventHandlerResult result = KeyEventHandler::ImeToAsciiEx(
        vk, key_info.GetScanCodeForMapVirtualKey(), is_key_down, keyboard_status,
        behavior, ime_state, mozc_context, private_context->GetClient(),
        keyboard.get(), &next_state, &temporal_output);

    if (!is_key_output_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    if (!result.succeeded) {
      // no message generated.
      *eaten = FALSE;
      return S_OK;
    }

    if (!is_key_output_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    *private_context->mutable_last_down_key() = next_state.last_down_key;

    TipInputModeManager* input_mode_manager =
        thread_context->GetInputModeManager();
    if (!is_key_output_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    const TipInputModeManager::Action action =
        input_mode_manager->OnKey(vk, is_key_down, result.should_be_eaten);
    if (!is_key_output_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    if (action == TipInputModeManager::Action::kUpdateUI) {
      text_service->PostUIUpdateMessage();
    }

    if (!is_key_output_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }
    const bool handled_noop_mode_indicator_key =
        UpdateNoOpModeIndicatorKeyState(
            text_service, private_context.get(), input_mode_manager,
            is_key_down,
            behavior, ime_state, result,
            /*is_on_key=*/true);
    if (!is_key_output_transaction_current()) {
      *eaten = TRUE;
      return S_OK;
    }

    if (!result.should_be_sent_to_server) {
      // no message generated.
      *eaten = handled_noop_mode_indicator_key ? TRUE : FALSE;
      return S_OK;
    }

    ignore_this_keyevent = handled_noop_mode_indicator_key
                               ? false
                               : !result.should_be_eaten;
  }

  if (!is_key_output_transaction_current()) {
    // This key was produced for a field that lost focus while a provider,
    // server, or IMM32-compatible handler was running. Consume it without
    // applying stale output to the newly focused field.
    *eaten = TRUE;
    return S_OK;
  }

  // TSF spec guarantees that key event handling can always be a synchronous
  // operation.
  TipEditSession::OnOutputReceivedSync(
      text_service, context, temporal_output, output_domain.focus_epoch,
      output_domain.focus_revision, output_application_generation);
  *eaten = !ignore_this_keyevent ? TRUE : FALSE;

  return S_OK;
}

constexpr bool kKeyDown = true;
constexpr bool kKeyUp = false;

}  // namespace

HRESULT TipKeyeventHandler::OnTestKeyDown(TipTextService* text_service,
                                          ITfContext* context, WPARAM wparam,
                                          LPARAM lparam, BOOL* eaten) {
  return OnTestKey(text_service, context, kKeyDown, wparam, lparam, eaten);
}

HRESULT TipKeyeventHandler::OnTestKeyUp(TipTextService* text_service,
                                        ITfContext* context, WPARAM wparam,
                                        LPARAM lparam, BOOL* eaten) {
  return OnTestKey(text_service, context, kKeyUp, wparam, lparam, eaten);
}

HRESULT TipKeyeventHandler::OnKeyDown(TipTextService* text_service,
                                      ITfContext* context, WPARAM wparam,
                                      LPARAM lparam, BOOL* eaten) {
  return OnKey(text_service, context, kKeyDown, wparam, lparam, eaten);
}

HRESULT TipKeyeventHandler::OnKeyUp(TipTextService* text_service,
                                    ITfContext* context, WPARAM wparam,
                                    LPARAM lparam, BOOL* eaten) {
  return OnKey(text_service, context, kKeyUp, wparam, lparam, eaten);
}

}  // namespace tsf
}  // namespace win32
}  // namespace mozc
