// Put text into whatever window has keyboard focus.
//
// Clipboard + Ctrl+V rather than synthesised keystrokes: it is instant for
// long text and handles Devanagari and emoji the same as ASCII. The user's
// previous clipboard text is restored shortly afterwards.
use std::thread;
use std::time::Duration;

use enigo::{Direction, Enigo, Key, Keyboard, Settings};

pub fn paste(text: &str) -> Result<(), String> {
    if text.trim().is_empty() {
        return Ok(());
    }
    let mut clipboard = arboard::Clipboard::new().map_err(|e| format!("clipboard unavailable: {e}"))?;
    let previous = clipboard.get_text().ok();
    clipboard
        .set_text(text.to_string())
        .map_err(|e| format!("could not write clipboard: {e}"))?;

    // Give the clipboard owner change a moment to settle before the paste.
    thread::sleep(Duration::from_millis(60));

    // Ctrl + the *virtual key* V. A Unicode 'v' event would reach the app as
    // typed text rather than as the paste shortcut.
    #[cfg(target_os = "windows")]
    let v_key = Key::Other(0x56); // VK_V
    #[cfg(not(target_os = "windows"))]
    let v_key = Key::Unicode('v');
    let mut enigo = Enigo::new(&Settings::default()).map_err(|e| format!("input simulation unavailable: {e}"))?;
    enigo.key(Key::Control, Direction::Press).map_err(|e| e.to_string())?;
    let result = enigo.key(v_key, Direction::Click).map_err(|e| e.to_string());
    let _ = enigo.key(Key::Control, Direction::Release);
    result?;

    if let Some(old) = previous {
        // Restore after the target app has read the clipboard.
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(600));
            if let Ok(mut cb) = arboard::Clipboard::new() {
                let _ = cb.set_text(old);
            }
        });
    }
    Ok(())
}
