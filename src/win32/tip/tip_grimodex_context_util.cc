// Copyright 2026 The Mozkey Authors

#include "win32/tip/tip_grimodex_context_util.h"

#include <msctf.h>
#include <wil/com.h>
#include <windows.h>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>

#include "base/win32/com.h"
#include "base/win32/wide_char.h"
#include "client/client_interface.h"
#include "grimodex/client_context.h"
#include "protocol/commands.pb.h"
#include "win32/tip/tip_grimodex_client_context.h"
#include "win32/tip/tip_input_mode_manager.h"
#include "win32/tip/tip_private_context.h"
#include "win32/tip/tip_surrounding_text.h"
#include "win32/tip/tip_text_service.h"
#include "win32/tip/tip_thread_context.h"

namespace mozc::win32::tsf {
namespace {

constexpr std::size_t kMaxSurroundingTextUtf16Length = 20;

bool IsSameActiveThreadContext(
    TipTextService* text_service,
    const std::shared_ptr<TipThreadContext>& thread_context) {
  if (text_service == nullptr || !thread_context) {
    return false;
  }
  const std::shared_ptr<TipThreadContext> current =
      text_service->GetThreadContextLease();
  return current && current.get() == thread_context.get();
}

bool IsHighSurrogate(wchar_t value) {
  const uint32_t code_unit = static_cast<uint16_t>(value);
  return 0xD800 <= code_unit && code_unit <= 0xDBFF;
}

bool IsLowSurrogate(wchar_t value) {
  const uint32_t code_unit = static_cast<uint16_t>(value);
  return 0xDC00 <= code_unit && code_unit <= 0xDFFF;
}

commands::Context BuildFailClosedTsfContext(
    TipThreadContext* thread_context) {
  const uint64_t focus_epoch =
      thread_context == nullptr
          ? 1
          : thread_context->ObserveGrimodexDomain(/*program=*/{},
                                                  /*secure_input=*/true);
  return BuildTsfClientContext(/*program=*/{}, /*secure_input=*/true,
                               focus_epoch);
}

}  // namespace

TsfFocusSnapshot CaptureTsfFocusSnapshot(
    const TipThreadContext* thread_context) {
  if (thread_context == nullptr) {
    return {};
  }
  return {
      .focus_epoch = thread_context->GetGrimodexFocusEpoch(),
      .focus_revision = thread_context->GetFocusRevision(),
  };
}

bool IsTsfFocusSnapshotCurrent(const TipThreadContext* thread_context,
                               TsfFocusSnapshot snapshot) {
  return thread_context != nullptr && snapshot.focus_epoch != 0 &&
         thread_context->GetGrimodexFocusEpoch() == snapshot.focus_epoch &&
         thread_context->GetFocusRevision() == snapshot.focus_revision;
}

bool IsNonSecureTsfFocusSnapshotCurrent(
    const TipThreadContext* thread_context, TsfFocusSnapshot snapshot) {
  return IsTsfFocusSnapshotCurrent(thread_context, snapshot) &&
         !thread_context->GetInputModeManager()->IsPasswordInputScope();
}

bool IsTsfContextFocusedForSnapshot(TipTextService* text_service,
                                    ITfContext* expected_context,
                                    TsfFocusSnapshot snapshot,
                                    bool require_nonsecure) {
  if (text_service == nullptr || expected_context == nullptr ||
      !text_service->HasThreadFocus()) {
    return false;
  }
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_context ||
      (require_nonsecure
           ? !IsNonSecureTsfFocusSnapshotCurrent(
                 thread_context.get(), snapshot)
           : !IsTsfFocusSnapshotCurrent(thread_context.get(), snapshot))) {
    return false;
  }
  wil::com_ptr_nothrow<ITfThreadMgr> thread_manager =
      text_service->GetThreadManager();
  if (!thread_manager ||
      !IsSameActiveThreadContext(text_service, thread_context)) {
    return false;
  }
  wil::com_ptr_nothrow<ITfDocumentMgr> document;
  if (FAILED(thread_manager->GetFocus(&document)) || document == nullptr ||
      !IsSameActiveThreadContext(text_service, thread_context)) {
    return false;
  }
  wil::com_ptr_nothrow<ITfContext> focused_context;
  if (FAILED(document->GetTop(&focused_context)) ||
      focused_context == nullptr ||
      !IsSameActiveThreadContext(text_service, thread_context)) {
    return false;
  }
  const auto expected_identity = ComQuery<IUnknown>(expected_context);
  const auto focused_identity = ComQuery<IUnknown>(focused_context.get());
  if (!expected_identity || !focused_identity ||
      expected_identity.get() != focused_identity.get() ||
      !IsSameActiveThreadContext(text_service, thread_context)) {
    return false;
  }
  return text_service->HasThreadFocus() &&
         (require_nonsecure
              ? IsNonSecureTsfFocusSnapshotCurrent(
                    thread_context.get(), snapshot)
              : IsTsfFocusSnapshotCurrent(thread_context.get(), snapshot));
}

std::wstring LimitTsfPrecedingText(std::wstring_view text) {
  if (text.size() <= kMaxSurroundingTextUtf16Length) {
    return std::wstring(text);
  }
  std::size_t begin = text.size() - kMaxSurroundingTextUtf16Length;
  if (begin > 0 && IsHighSurrogate(text[begin - 1]) &&
      IsLowSurrogate(text[begin])) {
    ++begin;
  }
  return std::wstring(text.substr(begin));
}

std::wstring LimitTsfFollowingText(std::wstring_view text) {
  if (text.size() <= kMaxSurroundingTextUtf16Length) {
    return std::wstring(text);
  }
  std::size_t length = kMaxSurroundingTextUtf16Length;
  if (length < text.size() && IsHighSurrogate(text[length - 1]) &&
      IsLowSurrogate(text[length])) {
    --length;
  }
  return std::wstring(text.substr(0, length));
}

std::string GetAttachedProgram(ITfContext* context) {
  if (context == nullptr) {
    return {};
  }
  wil::com_ptr_nothrow<ITfContextView> context_view;
  if (FAILED(context->GetActiveView(&context_view)) ||
      context_view == nullptr) {
    return {};
  }
  HWND attached_window = nullptr;
  if (FAILED(context_view->GetWnd(&attached_window)) ||
      attached_window == nullptr) {
    return {};
  }

  DWORD process_id = 0;
  if (::GetWindowThreadProcessId(attached_window, &process_id) == 0 ||
      process_id == 0) {
    return {};
  }
  HANDLE process = ::OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE,
                                 process_id);
  if (process == nullptr) {
    return {};
  }

  // QueryFullProcessImageNameW supports extended-length paths.  Keep one
  // fixed, bounded buffer so a hostile application cannot influence memory
  // growth through its executable path.
  std::wstring executable_path(32768, L'\0');
  DWORD executable_path_length =
      static_cast<DWORD>(executable_path.size());
  const BOOL query_succeeded = ::QueryFullProcessImageNameW(
      process, 0, executable_path.data(), &executable_path_length);
  ::CloseHandle(process);
  if (!query_succeeded || executable_path_length == 0) {
    return {};
  }
  executable_path.resize(executable_path_length);
  return NormalizeExecutableBasename(WideToUtf8(executable_path));
}

commands::Context BuildTsfMozcContext(TipTextService* text_service,
                                      ITfContext* context,
                                      bool include_surrounding_text) {
  if (text_service == nullptr) {
    return BuildFailClosedTsfContext(nullptr);
  }
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_context) {
    return BuildFailClosedTsfContext(nullptr);
  }
  if (context == nullptr) {
    return BuildFailClosedTsfContext(thread_context.get());
  }
  grimodex::SurroundingTextProvider surrounding_text_provider;
  if (include_surrounding_text) {
    surrounding_text_provider =
        [text_service, context]() -> std::optional<grimodex::SurroundingText> {
      TipSurroundingTextInfo info;
      if (!TipSurroundingText::Get(text_service, context, &info) ||
          (!info.has_preceding_text && !info.has_following_text)) {
        return std::nullopt;
      }
      grimodex::SurroundingText surrounding_text;
      if (info.has_preceding_text) {
        surrounding_text.preceding_text =
            WideToUtf8(LimitTsfPrecedingText(info.preceding_text));
      }
      if (info.has_following_text) {
        surrounding_text.following_text =
            WideToUtf8(LimitTsfFollowingText(info.following_text));
      }
      return surrounding_text;
    };
  }

  commands::Context result = BuildTsfMozcContextFromProviders(
      thread_context.get(), include_surrounding_text,
      [context]() { return GetAttachedProgram(context); },
      surrounding_text_provider);
  return IsSameActiveThreadContext(text_service, thread_context)
             ? result
             : BuildFailClosedTsfContext(nullptr);
}

commands::Context BuildTsfMozcContextFromProviders(
    TipThreadContext* thread_context, bool include_surrounding_text,
    const TsfAttachedProgramProvider& attached_program_provider,
    const grimodex::SurroundingTextProvider& surrounding_text_provider) {
  if (thread_context == nullptr) {
    return BuildFailClosedTsfContext(nullptr);
  }

  TipInputModeManager* input_mode_manager =
      thread_context->GetInputModeManager();
  const bool secure_input = input_mode_manager->IsPasswordInputScope();

  // Program resolution calls into TSF and Win32.  Capture both domain counters
  // before crossing that boundary, then reject even a successfully returned
  // executable name if focus or secure-input state changed reentrantly.
  const TsfFocusSnapshot before_program =
      CaptureTsfFocusSnapshot(thread_context);
  const std::string program =
      attached_program_provider ? attached_program_provider() : std::string();
  if (!IsTsfFocusSnapshotCurrent(thread_context, before_program) ||
      input_mode_manager->IsPasswordInputScope() != secure_input) {
    return BuildFailClosedTsfContext(thread_context);
  }

  const uint64_t focus_epoch =
      thread_context->ObserveGrimodexDomain(program, secure_input);
  const TsfFocusSnapshot surrounding_text_domain =
      CaptureTsfFocusSnapshot(thread_context);

  bool provider_domain_changed = false;
  grimodex::SurroundingTextProvider guarded_surrounding_text_provider;
  // Secure input is resolved before a text provider can be invoked.
  // BuildTsfClientContext independently refuses providers for PASSWORD fields.
  if (include_surrounding_text && !secure_input && surrounding_text_provider) {
    guarded_surrounding_text_provider =
        [&]() -> std::optional<grimodex::SurroundingText> {
      if (!IsTsfFocusSnapshotCurrent(thread_context, surrounding_text_domain) ||
          input_mode_manager->IsPasswordInputScope() != secure_input) {
        provider_domain_changed = true;
        return std::nullopt;
      }
      std::optional<grimodex::SurroundingText> surrounding_text =
          surrounding_text_provider();
      if (!IsTsfFocusSnapshotCurrent(thread_context, surrounding_text_domain) ||
          input_mode_manager->IsPasswordInputScope() != secure_input) {
        provider_domain_changed = true;
        return std::nullopt;
      }
      return surrounding_text;
    };
  }

  commands::Context result =
      BuildTsfClientContext(program, secure_input, focus_epoch,
                            guarded_surrounding_text_provider);
  if (provider_domain_changed) {
    return BuildFailClosedTsfContext(thread_context);
  }
  return result;
}

bool SendTsfResetContext(TipTextService* text_service, ITfContext* context,
                         client::ClientInterface* client,
                         bool force_secure_input,
                         TsfFocusSnapshot* installed_snapshot) {
  if (text_service == nullptr || context == nullptr || client == nullptr) {
    return false;
  }
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_context) {
    return false;
  }
  TipInputModeManager* input_mode_manager =
      thread_context->GetInputModeManager();
  const TsfFocusSnapshot reset_domain =
      CaptureTsfFocusSnapshot(thread_context.get());
  if (!IsTsfContextFocusedForSnapshot(text_service, context, reset_domain,
                                      /*require_nonsecure=*/false)) {
    return false;
  }
  std::string program = GetAttachedProgram(context);
  if (!IsSameActiveThreadContext(text_service, thread_context) ||
      !IsTsfContextFocusedForSnapshot(text_service, context, reset_domain,
                                      /*require_nonsecure=*/false)) {
    return false;
  }
  const bool secure_input = force_secure_input ||
                            input_mode_manager->IsPasswordInputScope();
  TsfFocusSnapshot installed_domain;
  if (!SendTsfResetContextForProgram(text_service, program, client,
                                     secure_input, &installed_domain)) {
    return false;
  }
  if (!IsTsfContextFocusedForSnapshot(text_service, context, installed_domain,
                                      /*require_nonsecure=*/false)) {
    return false;
  }
  if (installed_snapshot != nullptr) {
    *installed_snapshot = installed_domain;
  }
  return true;
}

bool SendTsfResetContextForProgram(TipTextService* text_service,
                                   std::string_view program,
                                   client::ClientInterface* client,
                                   bool secure_input,
                                   TsfFocusSnapshot* installed_snapshot) {
  if (text_service == nullptr || client == nullptr) {
    return false;
  }
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_context) {
    return false;
  }
  const uint64_t focus_epoch =
      thread_context->ObserveGrimodexDomain(program, secure_input);
  const TsfFocusSnapshot installed_domain = {
      .focus_epoch = focus_epoch,
      .focus_revision = thread_context->GetFocusRevision(),
  };
  const commands::Context client_context =
      BuildTsfClientContext(program, secure_input, focus_epoch);
  commands::SessionCommand command;
  command.set_type(commands::SessionCommand::RESET_CONTEXT);
  commands::Output output;
  if (!client->SendCommandWithContext(command, client_context, &output) ||
      !IsSameActiveThreadContext(text_service, thread_context) ||
      !IsTsfFocusSnapshotCurrent(thread_context.get(), installed_domain)) {
    return false;
  }
  if (installed_snapshot != nullptr) {
    *installed_snapshot = installed_domain;
  }
  return true;
}

bool SendTsfResetContextForInactiveClient(TipTextService* text_service,
                                          client::ClientInterface* client) {
  if (text_service == nullptr || client == nullptr) {
    return false;
  }
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_context) {
    return false;
  }
  const uint64_t current_epoch =
      thread_context->GetGrimodexFocusEpoch();
  const commands::Context client_context = BuildTsfClientContext(
      /*program=*/{}, /*secure_input=*/true,
      current_epoch == 0 ? 1 : current_epoch);
  commands::SessionCommand command;
  command.set_type(commands::SessionCommand::RESET_CONTEXT);
  commands::Output output;
  return client->SendCommandWithContext(command, client_context, &output) &&
         IsSameActiveThreadContext(text_service, thread_context);
}

bool SendTsfCommand(TipTextService* text_service, ITfContext* context,
                    client::ClientInterface* client,
                    const commands::SessionCommand& command,
                    commands::Output* output, TsfFocusSnapshot snapshot,
                    bool require_nonsecure,
                    uint64_t* output_application_generation) {
  if (text_service == nullptr || context == nullptr || client == nullptr ||
      output == nullptr) {
    return false;
  }
  std::shared_ptr<TipPrivateContext> private_context;
  if (output_application_generation != nullptr) {
    private_context = text_service->GetPrivateContext(context);
    if (!private_context) {
      return false;
    }
    if (*output_application_generation == 0) {
      if (!IsTsfContextFocusedForSnapshot(text_service, context, snapshot,
                                          require_nonsecure)) {
        return false;
      }
      *output_application_generation =
          private_context->ReserveOutputApplicationForFocusDomain(
              snapshot.focus_epoch, snapshot.focus_revision);
    } else if (!private_context->IsOutputApplicationForFocusDomain(
                   snapshot.focus_epoch, snapshot.focus_revision,
                   *output_application_generation)) {
      return false;
    }
  }
  if (!IsTsfContextFocusedForSnapshot(text_service, context, snapshot,
                                      require_nonsecure) ||
      (output_application_generation != nullptr &&
       !private_context->IsOutputApplicationForFocusDomain(
           snapshot.focus_epoch, snapshot.focus_revision,
           *output_application_generation))) {
    return false;
  }
  const std::shared_ptr<TipThreadContext> thread_context =
      text_service->GetThreadContextLease();
  if (!thread_context) {
    return false;
  }
  const commands::Context client_context = BuildTsfMozcContext(
      text_service, context, /*include_surrounding_text=*/false);
  if (!IsSameActiveThreadContext(text_service, thread_context) ||
      !IsTsfContextFocusedForSnapshot(text_service, context, snapshot,
                                      require_nonsecure) ||
      !client_context.has_grimodex() ||
      (require_nonsecure && client_context.grimodex().secure_input()) ||
      (output_application_generation != nullptr &&
       !private_context->IsOutputApplicationForFocusDomain(
           snapshot.focus_epoch, snapshot.focus_revision,
           *output_application_generation))) {
    return false;
  }
  if (!client->SendCommandWithContext(command, client_context, output)) {
    return false;
  }
  return IsSameActiveThreadContext(text_service, thread_context) &&
         IsTsfContextFocusedForSnapshot(text_service, context, snapshot,
                                        require_nonsecure) &&
         (output_application_generation == nullptr ||
          private_context->IsOutputApplicationForFocusDomain(
              snapshot.focus_epoch, snapshot.focus_revision,
              *output_application_generation));
}

}  // namespace mozc::win32::tsf
