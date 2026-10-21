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

use std::str::FromStr;
use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, State,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

pub struct Dictation(Mutex<Option<dictation::Session>>);
pub struct Hotkey(Mutex<Option<Shortcut>>);

pub const DEFAULT_HOTKEY: &str = "Ctrl+Shift+KeyM";
const TRAY_ID: &str = "main";

/// Register `combo` as the global start/stop hotkey, replacing the previous
/// one. Accepts the global-shortcut syntax: modifiers `Ctrl`, `Shift`, `Alt`,
/// `Super` joined with `+` to a W3C key code such as `KeyM`, `F8`, `Space`.
fn apply_hotkey(app: &AppHandle, combo: &str) -> Result<(), String> {
    let shortcut = Shortcut::from_str(combo).map_err(|e| format!("Not a usable key combination: {e}"))?;
    let gs = app.global_shortcut();
    let state: State<Hotkey> = app.state();
    let mut current = state.0.lock().map_err(|_| "hotkey state poisoned")?;
    if let Some(old) = current.take() {
        let _ = gs.unregister(old);
    }
    gs.on_shortcut(shortcut, |app, _shortcut, event| {
        if event.state() == ShortcutState::Pressed {
            let _ = app.emit("hotkey", ());
        }
    })
    .map_err(|e| format!("Windows refused that hotkey (another app may own it): {e}"))?;
    *current = Some(shortcut);
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.set_tooltip(Some(format!("Voice Notes ({} to dictate)", pretty_hotkey(combo))));
    }
    Ok(())
}

/// "Ctrl+Shift+KeyM" -> "Ctrl+Shift+M" for tooltips.
fn pretty_hotkey(combo: &str) -> String {
    combo
        .split('+')
        .map(|part| part.strip_prefix("Key").or_else(|| part.strip_prefix("Digit")).unwrap_or(part))
        .collect::<Vec<_>>()
        .join("+")
}

#[tauri::command]
fn set_hotkey(app: AppHandle, combo: String) -> Result<String, String> {
    apply_hotkey(&app, combo.trim())?;
    Ok(pretty_hotkey(combo.trim()))
}

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
        .manage(Hotkey(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            start_dictation,
            stop_dictation,
            paste_text,
            hide_window,
            mic_name,
            set_hotkey
        ])
        .setup(|app| {
            // Tray icon with Show / Quit.
            let show = MenuItem::with_id(app, "show", "Show Voice Notes", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::with_id(TRAY_ID)
                .icon(app.default_window_icon().cloned().expect("window icon"))
                .tooltip("Voice Notes")
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

            // Default global hotkey; the webview replaces it with the saved
            // one once it loads. The webview decides what "toggle" means.
            apply_hotkey(app.handle(), DEFAULT_HOTKEY).map_err(|e| std::io::Error::other(e))?;
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
