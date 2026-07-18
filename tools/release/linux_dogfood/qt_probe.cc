// Copyright 2026 The Mozkey Authors
// Licensed under the Apache License, Version 2.0.

#include <QApplication>
#include <QGuiApplication>
#include <QLineEdit>
#include <QTimer>

#include <cstdlib>
#include <cstring>
#include <iostream>

int main(int argc, char **argv) {
  const char *expected_environment =
      std::getenv("MOZKEY_DOGFOOD_EXPECTED_VALUE");
  if (expected_environment == nullptr || *expected_environment == '\0') {
    std::cerr << "MOZKEY_DOGFOOD_EXPECTED_VALUE is required" << std::endl;
    return 2;
  }
  int timeout_seconds = 60;
  if (const char *timeout_environment =
          std::getenv("MOZKEY_DOGFOOD_TIMEOUT_SECONDS")) {
    char *end = nullptr;
    const long parsed = std::strtol(timeout_environment, &end, 10);
    if (end == timeout_environment || *end != '\0' || parsed < 1 ||
        parsed > 300) {
      std::cerr << "MOZKEY_DOGFOOD_TIMEOUT_SECONDS is invalid" << std::endl;
      return 2;
    }
    timeout_seconds = static_cast<int>(parsed);
  }
  const char *password_environment =
      std::getenv("MOZKEY_DOGFOOD_PASSWORD");
  if (password_environment != nullptr &&
      std::strcmp(password_environment, "1") != 0) {
    std::cerr << "MOZKEY_DOGFOOD_PASSWORD must be 1 when set" << std::endl;
    return 2;
  }
  QApplication application(argc, argv);
  QLineEdit entry;
  const bool password = password_environment != nullptr;
  const QString expected = QString::fromUtf8(expected_environment);
  bool ready_emitted = false;
  bool result_emitted = false;
  int exit_status = 2;

  entry.setWindowTitle(password ? "Mozkey Qt Password Probe"
                                : "Mozkey Qt Probe");
  entry.setPlaceholderText("Type with Mozkey, then press Enter");
  entry.resize(720, 120);
  if (password) {
    entry.setEchoMode(QLineEdit::Password);
  }
  QObject::connect(&entry, &QLineEdit::returnPressed, [&]() {
    if (result_emitted) {
      return;
    }
    const bool match = entry.text() == expected;
    std::cout << "RESULT:match=" << (match ? "true" : "false")
              << " length=" << entry.text().toUcs4().size()
              << " password=" << (password ? "true" : "false")
              << std::endl;
    result_emitted = true;
  });

  const auto maybe_emit_ready = [&]() {
    if (!ready_emitted && entry.isActiveWindow() && entry.hasFocus()) {
      ready_emitted = true;
      std::cout << "READY:active=true focused=true password="
                << (password ? "true" : "false") << std::endl;
    }
  };
  QObject::connect(&application, &QApplication::focusChanged,
                   [&](QWidget *, QWidget *) { maybe_emit_ready(); });
  QObject::connect(&application, &QGuiApplication::applicationStateChanged,
                   [&](Qt::ApplicationState) { maybe_emit_ready(); });
  entry.show();
  entry.raise();
  entry.activateWindow();
  entry.setFocus(Qt::OtherFocusReason);
  QTimer::singleShot(0, maybe_emit_ready);
  QTimer::singleShot(timeout_seconds * 1000, [&]() {
    if (result_emitted) {
      return;
    }
    std::cerr << "RESULT:match=false reason=timeout password="
              << (password ? "true" : "false") << std::endl;
    exit_status = 2;
    application.exit(exit_status);
  });
  const int application_status = application.exec();
  return application_status == 0 ? exit_status : application_status;
}
