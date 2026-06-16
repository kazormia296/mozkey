#include "renderer/win32/ruby_window.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>

#include "base/win32/wide_char.h"
#include "config/config_handler.h"
#include "protocol/commands.pb.h"
#include "protocol/config.pb.h"
#include "protocol/renderer_command.pb.h"
#include "renderer/renderer_style_handler.h"
#include "renderer/win32/win32_dpi_util.h"
#include "renderer/win32/win32_renderer_util.h"

namespace mozc {
namespace renderer {
namespace win32 {
namespace {

// Theme-aware translucent pill UI.
constexpr int kPaddingX = 14;
constexpr int kPaddingY = 6;
constexpr int kGapFromComposition = 0;
constexpr int kFontPointSize = 13;
constexpr uint32_t kDefaultDpi = 96;

// Keep the overlay close to the preedit, but do not let it cover the glyphs.
constexpr int kFallbackLineHeight = 22;

COLORREF ToColorRef(uint32_t rgb) {
  return RGB(static_cast<int>((rgb >> 16) & 0xff),
             static_cast<int>((rgb >> 8) & 0xff),
             static_cast<int>(rgb & 0xff));
}

RendererStyleHandler::RubyWindowStyle GetRubyWindowTheme() {
  return RendererStyleHandler::GetRubyWindowStyle();
}

uint32_t GetWindowDpi(HWND hwnd) {
  return GetDpiForWindowHandle(hwnd);
}

int ScaleCornerRadius(uint32_t logical_radius, uint32_t dpi) {
  if (logical_radius == 0) {
    return 0;
  }
  return static_cast<int>(
      std::lround(logical_radius *
                  (static_cast<double>(dpi) / static_cast<double>(kDefaultDpi))));
}

int ScaleByPercent(int value, uint32_t percent) {
  if (value <= 0) {
    return 0;
  }
  return std::max(1, static_cast<int>(std::lround(
                         static_cast<double>(value) * percent / 100.0)));
}

int GetRubyPaddingX(const RendererStyleHandler::RubyWindowStyle& theme) {
  return ScaleByPercent(kPaddingX, theme.size_percent);
}

int GetRubyPaddingY(const RendererStyleHandler::RubyWindowStyle& theme) {
  return ScaleByPercent(kPaddingY, theme.size_percent);
}

int GetRubyFontPointSize() {
  return ScaleByPercent(
      kFontPointSize, RendererStyleHandler::GetRubyWindowStyle().size_percent);
}

bool IsLiveConversionRubyWindowEnabled() {
  const auto config = config::ConfigHandler::GetSharedConfig();
  return config == nullptr || config->show_live_conversion_ruby_window();
}

std::wstring GetRubyWindowFontFaceName() {
  RendererStyle style;
  if (!RendererStyleHandler::GetRendererStyle(&style)) {
    return L"Yu Gothic UI";
  }

  const RendererStyle::TextStyle& candidate_style = style.candidate_style();
  if (!candidate_style.has_font_name() || candidate_style.font_name().empty()) {
    return L"Yu Gothic UI";
  }

  const std::wstring font_name =
      mozc::win32::Utf8ToWide(candidate_style.font_name());
  if (font_name.empty()) {
    return L"Yu Gothic UI";
  }

  if (font_name.front() == L'@' || font_name.size() >= LF_FACESIZE) {
    return L"Yu Gothic UI";
  }

  return font_name;
}

std::wstring ToWide(const std::string& text) {
  return mozc::win32::Utf8ToWide(text);
}

RECT GetWorkAreaForPoint(const POINT& point) {
  RECT work_area = {};
  HMONITOR monitor = ::MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST);

  MONITORINFO monitor_info = {};
  monitor_info.cbSize = sizeof(monitor_info);
  if (::GetMonitorInfo(monitor, &monitor_info)) {
    return monitor_info.rcWork;
  }

  ::SystemParametersInfo(SPI_GETWORKAREA, 0, &work_area, 0);
  return work_area;
}

int ClampInt(int value, int min_value, int max_value) {
  if (max_value < min_value) {
    return min_value;
  }
  return std::min(std::max(value, min_value), max_value);
}

int GetPreeditWidth(const commands::RendererCommand& command) {
  if (!command.has_preedit_rectangle()) {
    return 0;
  }

  const commands::RendererCommand::Rectangle& rect =
      command.preedit_rectangle();
  return std::max(0, rect.right() - rect.left());
}

}  // namespace

RubyWindow::RubyWindow() = default;

RubyWindow::~RubyWindow() {
  ResetFont();
}

void RubyWindow::Initialize() {
  if (!IsWindow()) {
    Create(nullptr);
  }

  ApplyRendererWindowOpacity(
      m_hWnd, RendererStyleHandler::GetRubyWindowStyle().opacity_percent);

  ShowWindow(SW_HIDE);
}

void RubyWindow::Destroy() {
  shadow_window_.Destroy();
  if (IsWindow()) {
    DestroyWindow();
  }
}

void RubyWindow::Hide() {
  shadow_window_.Hide();
  if (IsWindow()) {
    ShowWindow(SW_HIDE);
  }
}

bool RubyWindow::BuildReadingText(
    const commands::RendererCommand& command,
    std::string* reading) const {
  reading->clear();

  if (!command.has_output()) {
    return false;
  }

  const commands::Output& output = command.output();
  if (!output.live_conversion()) {
    return false;
  }

  if (!output.has_preedit()) {
    return false;
  }

  const commands::Preedit& preedit = output.preedit();

  std::string value;
  for (int i = 0; i < preedit.segment_size(); ++i) {
    const commands::Preedit::Segment& segment = preedit.segment(i);

    value.append(segment.value());

    if (segment.has_key() && !segment.key().empty()) {
      reading->append(segment.key());
    } else {
      reading->append(segment.value());
    }
  }

  if (reading->empty()) {
    return false;
  }

  // Always show the ruby overlay during live conversion, even when the
  // visible text and reading are identical.  This keeps the user's raw kana
  // input visible while live conversion is active.
  return true;
}

bool RubyWindow::GetBasePosition(const commands::RendererCommand& command,
                                 POINT* point,
                                 int* line_height) const {
  // Prefer the preedit rectangle. This is usually closer to the actual
  // composition text than composition_target, which tends to follow the caret.
  if (command.has_preedit_rectangle()) {
    const commands::RendererCommand::Rectangle& rect =
        command.preedit_rectangle();

    const int height = rect.bottom() - rect.top();
    if (height > 0) {
      point->x = rect.left();
      point->y = rect.top();
      *line_height = height;
      return true;
    }
  }

  // Fallback for clients that do not provide a usable preedit rectangle.
  if (command.has_application_info()) {
    const commands::RendererCommand::ApplicationInfo& app_info =
        command.application_info();

    if (app_info.has_composition_target() &&
        app_info.composition_target().has_top_left()) {
      const commands::RendererCommand::CharacterPosition& target =
          app_info.composition_target();

      point->x = target.top_left().x();
      point->y = target.top_left().y();
      *line_height = target.has_line_height()
                         ? static_cast<int>(target.line_height())
                         : kFallbackLineHeight;
      return true;
    }
  }

  return false;
}

void RubyWindow::ResetFont() {
  if (font_ != nullptr) {
    ::DeleteObject(font_);
    font_ = nullptr;
  }

  font_face_name_.clear();
  font_height_ = 0;
  font_weight_ = 0;
  font_dpi_y_ = 0;
}

void RubyWindow::UpdateFont(HDC dc) {
  const int dpi_y = ::GetDeviceCaps(dc, LOGPIXELSY);

  const std::wstring font_name = GetRubyWindowFontFaceName();
  const int font_height = -MulDiv(GetRubyFontPointSize(), dpi_y, 72);
  const int font_weight = FW_SEMIBOLD;

  if (font_ != nullptr &&
      font_dpi_y_ == dpi_y &&
      font_height_ == font_height &&
      font_weight_ == font_weight &&
      font_face_name_ == font_name) {
    return;
  }

  ResetFont();

  LOGFONTW logfont = {};
  logfont.lfHeight = font_height;
  logfont.lfWeight = font_weight;
  logfont.lfQuality = CLEARTYPE_QUALITY;
  logfont.lfCharSet = DEFAULT_CHARSET;
  logfont.lfPitchAndFamily = DEFAULT_PITCH | FF_DONTCARE;
  wcscpy_s(logfont.lfFaceName, font_name.c_str());

  font_ = ::CreateFontIndirectW(&logfont);

  std::wstring actual_font_name = font_name;
  if (font_ == nullptr && font_name != L"Yu Gothic UI") {
    wcscpy_s(logfont.lfFaceName, L"Yu Gothic UI");
    font_ = ::CreateFontIndirectW(&logfont);
    actual_font_name = L"Yu Gothic UI";
  }

  if (font_ == nullptr) {
    return;
  }

  font_face_name_ = actual_font_name;
  font_height_ = font_height;
  font_weight_ = font_weight;
  font_dpi_y_ = dpi_y;
}

SIZE RubyWindow::MeasureText() const {
  SIZE size = {};
  if (text_.empty()) {
    return size;
  }

  HDC dc = ::GetDC(nullptr);

  HFONT old_font = nullptr;
  if (font_ != nullptr) {
    old_font = static_cast<HFONT>(::SelectObject(dc, font_));
  }

  ::GetTextExtentPoint32W(dc, text_.data(), static_cast<int>(text_.size()),
                          &size);

  TEXTMETRICW text_metric = {};
  if (::GetTextMetricsW(dc, &text_metric)) {
    size.cy = std::max<LONG>(size.cy, text_metric.tmHeight);
  }

  if (old_font != nullptr) {
    ::SelectObject(dc, old_font);
  }

  ::ReleaseDC(nullptr, dc);

  const RendererStyleHandler::RubyWindowStyle theme = GetRubyWindowTheme();
  size.cx += GetRubyPaddingX(theme) * 2;
  size.cy += GetRubyPaddingY(theme) * 2;
  return size;
}

void RubyWindow::OnUpdate(const commands::RendererCommand& command) {
  if (!command.visible()) {
    Hide();
    return;
  }

  if (!IsLiveConversionRubyWindowEnabled()) {
    Hide();
    return;
  }

  std::string reading;
  if (!BuildReadingText(command, &reading)) {
    Hide();
    return;
  }

  POINT base_point = {};
  int line_height = kFallbackLineHeight;
  if (!GetBasePosition(command, &base_point, &line_height)) {
    Hide();
    return;
  }

  HDC dc = ::GetDC(nullptr);
  UpdateFont(dc);
  ::ReleaseDC(nullptr, dc);

  text_ = ToWide(reading);
  window_size_ = MeasureText();
  if (window_size_.cx <= 0 || window_size_.cy <= 0) {
    Hide();
    return;
  }

  const RECT work_area = GetWorkAreaForPoint(base_point);

  // Horizontally center over the preedit rectangle when possible.
  // If preedit width is unavailable, align to the preedit/caret x position.
  const int preedit_width = GetPreeditWidth(command);

  int left = base_point.x;
  if (preedit_width > 0) {
    left = base_point.x + (preedit_width - window_size_.cx) / 2;
  }

  // Attach tightly above the preedit.
  int top = base_point.y - window_size_.cy - kGapFromComposition;

  // If there is no room above, place below the composition line.
  if (top < work_area.top) {
    top = base_point.y + line_height + kGapFromComposition;
  }

  left = ClampInt(left, work_area.left, work_area.right - window_size_.cx);
  top = ClampInt(top, work_area.top, work_area.bottom - window_size_.cy);

  const int radius = ScaleCornerRadius(
      RendererStyleHandler::GetRubyWindowStyle().corner_radius,
      GetWindowDpi(m_hWnd));
  if (radius <= 0) {
    ::SetWindowRgn(m_hWnd, nullptr, TRUE);
  } else {
    HRGN region = ::CreateRoundRectRgn(0, 0, window_size_.cx + 1,
                                       window_size_.cy + 1, radius * 2,
                                       radius * 2);
    ::SetWindowRgn(m_hWnd, region, TRUE);
    // Ownership of |region| is transferred to the window.
  }

  SetWindowPos(HWND_TOPMOST, left, top, window_size_.cx, window_size_.cy,
               SWP_NOACTIVATE | SWP_SHOWWINDOW);
  ApplyRendererWindowOpacity(m_hWnd, GetRubyWindowTheme().opacity_percent);
  RECT window_rect = {left, top, left + window_size_.cx, top + window_size_.cy};
  RendererWindowShadowStyle shadow_style;
  shadow_style.size = GetRubyWindowTheme().shadow.size;
  shadow_style.opacity_percent = GetRubyWindowTheme().shadow.opacity_percent;
  shadow_style.angle_degrees = GetRubyWindowTheme().shadow.angle_degrees;
  shadow_style.distance = GetRubyWindowTheme().shadow.distance;
  shadow_window_.Update(m_hWnd, window_rect, GetWindowDpi(m_hWnd),
                        GetRubyWindowTheme().corner_radius, shadow_style);

  ShowWindow(SW_SHOWNOACTIVATE);
  Invalidate(FALSE);
}

LRESULT RubyWindow::OnEraseBkgnd(UINT msg_id,
                                 WPARAM wparam,
                                 LPARAM lparam,
                                 BOOL& handled) {
  handled = TRUE;
  return TRUE;
}

LRESULT RubyWindow::OnShowWindow(UINT /*msg_id*/, WPARAM wparam,
                                 LPARAM /*lparam*/, BOOL& /*handled*/) {
  if (wparam == FALSE) {
    shadow_window_.Hide();
  }
  return 0;
}

LRESULT RubyWindow::OnPaint(UINT msg_id,
                            WPARAM wparam,
                            LPARAM lparam,
                            BOOL& handled) {
  PAINTSTRUCT ps = {};
  HDC dc = BeginPaint(&ps);
  DoPaint(dc);
  EndPaint(&ps);
  return 0;
}

void RubyWindow::DoPaint(HDC dc) {
  RECT rect = {};
  GetClientRect(&rect);

  const RendererStyleHandler::RubyWindowStyle theme = GetRubyWindowTheme();

  ::SetBkMode(dc, TRANSPARENT);

  HBRUSH bg_brush = ::CreateSolidBrush(ToColorRef(theme.background_color));
  HPEN border_pen = ::CreatePen(PS_SOLID, 1, ToColorRef(theme.border_color));

  HGDIOBJ old_brush = ::SelectObject(dc, bg_brush);
  HGDIOBJ old_pen = ::SelectObject(dc, border_pen);

  const int radius = ScaleCornerRadius(theme.corner_radius, GetWindowDpi(m_hWnd));
  if (radius <= 0) {
    ::Rectangle(dc, rect.left, rect.top, rect.right, rect.bottom);
  } else {
    ::RoundRect(dc, rect.left, rect.top, rect.right, rect.bottom, radius * 2,
                radius * 2);
  }

  ::SelectObject(dc, old_pen);
  ::SelectObject(dc, old_brush);
  ::DeleteObject(border_pen);
  ::DeleteObject(bg_brush);

  HFONT old_font = nullptr;
  if (font_ != nullptr) {
    old_font = static_cast<HFONT>(::SelectObject(dc, font_));
  }

  ::SetTextColor(dc, ToColorRef(theme.text_color));

  RECT text_rect = rect;
  text_rect.left += GetRubyPaddingX(theme);
  text_rect.top += GetRubyPaddingY(theme);
  text_rect.right -= GetRubyPaddingX(theme);
  text_rect.bottom -= GetRubyPaddingY(theme);

  ::DrawTextW(dc, text_.c_str(), static_cast<int>(text_.size()), &text_rect,
              DT_CENTER | DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX);

  if (old_font != nullptr) {
    ::SelectObject(dc, old_font);
  }
}

}  // namespace win32
}  // namespace renderer
}  // namespace mozc
