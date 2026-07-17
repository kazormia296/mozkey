// Copyright 2026 The Mozkey Authors
// Licensed under the Apache License, Version 2.0.

#include <gtk/gtk.h>

#include <stdio.h>

typedef struct {
  GtkApplication *application;
  GtkWindow *window;
  GtkWidget *entry;
  const char *expected;
  gboolean password;
  gboolean ready_emitted;
  gboolean result_emitted;
  int exit_status;
} ProbeState;

static void maybe_emit_ready(ProbeState *state) {
  if (state->ready_emitted || !gtk_window_is_active(state->window) ||
      !gtk_widget_has_focus(state->entry)) {
    return;
  }
  state->ready_emitted = TRUE;
  g_print("READY:active=true focused=true password=%s\n",
          state->password ? "true" : "false");
  fflush(stdout);
}

static void on_focus_state_changed(GObject *object, GParamSpec *parameter,
                                   gpointer user_data) {
  (void)object;
  (void)parameter;
  maybe_emit_ready((ProbeState *)user_data);
}

static void on_entry_activate(GtkEntry *entry, gpointer user_data) {
  ProbeState *state = (ProbeState *)user_data;
  if (state->result_emitted) {
    return;
  }
  const char *text = gtk_editable_get_text(GTK_EDITABLE(entry));
  const gboolean match = g_strcmp0(text, state->expected) == 0;
  const glong length = g_utf8_strlen(text, -1);
  g_print("RESULT:match=%s length=%ld password=%s\n",
          match ? "true" : "false", length,
          state->password ? "true" : "false");
  fflush(stdout);
  state->result_emitted = TRUE;
}

static gboolean on_timeout(gpointer user_data) {
  ProbeState *state = (ProbeState *)user_data;
  if (state->result_emitted) {
    return G_SOURCE_REMOVE;
  }
  g_printerr("RESULT:match=false reason=timeout password=%s\n",
             state->password ? "true" : "false");
  state->exit_status = 2;
  g_application_quit(G_APPLICATION(state->application));
  return G_SOURCE_REMOVE;
}

static void on_activate(GtkApplication *application, gpointer user_data) {
  ProbeState *state = (ProbeState *)user_data;
  if (state->window != NULL) {
    gtk_window_present(state->window);
    gtk_widget_grab_focus(state->entry);
    maybe_emit_ready(state);
    return;
  }
  GtkWidget *window = gtk_application_window_new(application);
  GtkWidget *entry = gtk_entry_new();

  state->application = application;
  state->window = GTK_WINDOW(window);
  state->entry = entry;

  gtk_window_set_title(GTK_WINDOW(window),
                       state->password ? "Mozkey GTK Password Probe"
                                       : "Mozkey GTK Probe");
  gtk_window_set_default_size(GTK_WINDOW(window), 720, 120);
  gtk_entry_set_placeholder_text(GTK_ENTRY(entry),
                                 "Type with Mozkey, then press Enter");
  if (state->password) {
    gtk_entry_set_visibility(GTK_ENTRY(entry), FALSE);
    gtk_entry_set_input_purpose(GTK_ENTRY(entry), GTK_INPUT_PURPOSE_PASSWORD);
  } else {
    gtk_entry_set_input_purpose(GTK_ENTRY(entry), GTK_INPUT_PURPOSE_FREE_FORM);
  }
  g_signal_connect(entry, "activate", G_CALLBACK(on_entry_activate), state);
  g_signal_connect(window, "notify::is-active",
                   G_CALLBACK(on_focus_state_changed), state);
  g_signal_connect(entry, "notify::has-focus",
                   G_CALLBACK(on_focus_state_changed), state);
  gtk_window_set_child(GTK_WINDOW(window), entry);
  gtk_window_present(GTK_WINDOW(window));
  gtk_widget_grab_focus(entry);
  maybe_emit_ready(state);
}

int main(int argc, char **argv) {
  const char *expected = g_getenv("MOZKEY_DOGFOOD_EXPECTED_VALUE");
  if (expected == NULL || *expected == '\0') {
    g_printerr("MOZKEY_DOGFOOD_EXPECTED_VALUE is required\n");
    return 2;
  }
  guint timeout_seconds = 60;
  const char *timeout_environment = g_getenv("MOZKEY_DOGFOOD_TIMEOUT_SECONDS");
  if (timeout_environment != NULL) {
    char *end = NULL;
    const gint64 parsed = g_ascii_strtoll(timeout_environment, &end, 10);
    if (end == timeout_environment || *end != '\0' || parsed < 1 ||
        parsed > 300) {
      g_printerr("MOZKEY_DOGFOOD_TIMEOUT_SECONDS is invalid\n");
      return 2;
    }
    timeout_seconds = (guint)parsed;
  }
  const char *application_id = g_getenv("MOZKEY_DOGFOOD_APP_ID");
  if (application_id == NULL || *application_id == '\0') {
    application_id = "com.miyakey.mozkey.GtkDogfood";
  }
  const char *password_environment = g_getenv("MOZKEY_DOGFOOD_PASSWORD");
  if (password_environment != NULL &&
      g_strcmp0(password_environment, "1") != 0) {
    g_printerr("MOZKEY_DOGFOOD_PASSWORD must be 1 when set\n");
    return 2;
  }
  ProbeState state = {
      .expected = expected,
      .password = password_environment != NULL,
      .exit_status = 2,
  };
  GtkApplication *application =
      gtk_application_new(application_id, G_APPLICATION_DEFAULT_FLAGS);
  state.application = application;
  g_signal_connect(application, "activate", G_CALLBACK(on_activate), &state);
  g_timeout_add_seconds(timeout_seconds, on_timeout, &state);
  const int application_status =
      g_application_run(G_APPLICATION(application), argc, argv);
  g_object_unref(application);
  return application_status == 0 ? state.exit_status : application_status;
}
