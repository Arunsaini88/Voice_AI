// Voice Notes desktop shell.
//
// The UI is the same React app as the web version, loaded into a webview.
// This crate adds what a browser cannot do:
//   * a global hotkey (Ctrl+Shift+M) that works while any app has focus
//   * microphone capture streamed to the backend from Rust (dictation.rs),
//     so it works without webview media permissions and can be fed a WAV
//     file for testing (VN_FAKE_MIC=path)
//   * pasting text into whichever window has the cursor (paste.rs)
//   * a tray icon; closing the window hides it instead of quitting
mod dictation;
mod paste;

use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, State,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

pub struct Dictation(Mutex<Option<dictation::Session>>);

/// Start streaming the microphone to `api_url`'s dictation WebSocket.
/// Transcript events arrive in the webview as `dictation` events with the
/// same JSON the browser client receives (partial / final / limit / error /
/// done), plus `closed` when the session has fully ended.
#[tauri::command]
fn start_dictation(
    app: AppHandle,
    state: State<Dictation>,
    api_url: String,
    token: String,
    lang: String,
) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|_| "state poisoned")?;
    if let Some(existing) = guard.as_ref() {
        if !existing.is_done() {
            return Err("A recording is still finishing. Try again in a moment.".into());
        }
    }
    *guard = Some(dictation::Session::start(app, api_url, token, lang)?);
    Ok(())
}

#[tauri::command]
fn stop_dictation(state: State<Dictation>) -> Result<(), String> {
    if let Some(session) = state.0.lock().map_err(|_| "state poisoned")?.as_ref() {
        session.stop();
    }
    Ok(())
}

/// Put `text` into the window that currently has keyboard focus. The
/// frontend hides our own window first so focus returns to the user's app.
#[tauri::command]
fn paste_text(text: String) -> Result<(), String> {
    paste::paste(&text)
}

#[tauri::command]
fn hide_window(app: AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.hide();
    }
}

#[tauri::command]
fn mic_name() -> Option<String> {
    dictation::default_mic_name()
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(Dictation(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            start_dictation,
            stop_dictation,
            paste_text,
            hide_window,
            mic_name
        ])
        .setup(|app| {
            // Global hotkey: the webview decides what "toggle" means (start or stop).
            let shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyM);
            app.global_shortcut().on_shortcut(shortcut, |app, _shortcut, event| {
                if event.state() == ShortcutState::Pressed {
                    let _ = app.emit("hotkey", ());
                }
            })?;

            // Tray icon with Show / Quit.
            let show = MenuItem::with_id(app, "show", "Show Voice Notes", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().cloned().expect("window icon"))
                .tooltip("Voice Notes (Ctrl+Shift+M to dictate)")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                        show_main(tray.app_handle());
                    }
                })
                .build(app)?;
            Ok(())
        })
        // Closing the window keeps the app (and the hotkey) alive in the tray.
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Voice Notes");
}
