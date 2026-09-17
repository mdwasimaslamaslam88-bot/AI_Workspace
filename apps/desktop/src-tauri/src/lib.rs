use keyring::{Entry, Error as KeyringError};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, WindowEvent,
};
use tauri_plugin_autostart::MacosLauncher;

const CREDENTIAL_SERVICE: &str = "com.workstation.personalai";
const CREDENTIAL_ACCOUNT: &str = "owner-session";
const DESKTOP_SMOKE_INSTANCE_PREFIX: &str = "com.workstation.personalai.desktop_smoke.";

fn desktop_smoke_instance_id() -> Option<String> {
    let candidate = std::env::var("ASTER_DESKTOP_SMOKE_INSTANCE_ID").ok()?;
    let suffix = candidate.strip_prefix(DESKTOP_SMOKE_INSTANCE_PREFIX)?;
    if suffix.is_empty()
        || !suffix
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '_')
    {
        return None;
    }
    Some(candidate)
}

fn credential_entry() -> Result<Entry, String> {
    Entry::new(CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT)
        .map_err(|_| "Secure session storage is unavailable.".to_owned())
}

fn validate_session_token(token: &str) -> Result<(), String> {
    if token.is_empty() || token.len() > 512 {
        return Err("A valid session token is required.".to_owned());
    }
    Ok(())
}

#[tauri::command]
fn read_session_token() -> Result<Option<String>, String> {
    match credential_entry()?.get_password() {
        Ok(token) if !token.is_empty() => Ok(Some(token)),
        Ok(_) | Err(KeyringError::NoEntry) => Ok(None),
        Err(_) => Err("Secure session storage is unavailable.".to_owned()),
    }
}

#[tauri::command]
fn write_session_token(token: String) -> Result<(), String> {
    validate_session_token(&token)?;
    credential_entry()?
        .set_password(&token)
        .map_err(|_| "Secure session storage is unavailable.".to_owned())
}

#[tauri::command]
fn clear_session_token() -> Result<(), String> {
    match credential_entry()?.delete_credential() {
        Ok(()) | Err(KeyringError::NoEntry) => Ok(()),
        Err(_) => Err("Secure session storage is unavailable.".to_owned()),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();

    #[cfg(desktop)]
    {
        // The release smoke runs beside the user's real instance.  It keeps
        // the normal desktop session bus (needed by the tray/portal stack)
        // but can request a distinct instance name so it never talks to or
        // terminates the user's application.  Normal launches retain the
        // canonical identifier from tauri.conf.json.
        if let Some(smoke_instance_id) = desktop_smoke_instance_id() {
            builder = builder.plugin(
                tauri_plugin_single_instance::Builder::new()
                    .dbus_id(smoke_instance_id)
                    .callback(|app, _argv, _cwd| {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    })
                    .build(),
            );
        } else {
            builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }));
        }
    }

    builder
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--background"]),
        ))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .setup(|app| {
            let show = MenuItem::with_id(app, "show", "Open WORK STATION", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            let mut tray = TrayIconBuilder::new()
                .tooltip("WORK STATION")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.unminimize();
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            read_session_token,
            write_session_token,
            clear_session_token
        ])
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                #[cfg(not(target_os = "macos"))]
                {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("WORK STATION desktop runtime failed");
}

#[cfg(test)]
mod tests {
    use super::{desktop_smoke_instance_id, validate_session_token};

    #[test]
    fn secure_session_contract_accepts_bounded_opaque_tokens() {
        assert!(validate_session_token("opaque-owner-session").is_ok());
        assert!(validate_session_token(&"x".repeat(512)).is_ok());
    }

    #[test]
    fn secure_session_contract_rejects_empty_or_oversized_values() {
        assert!(validate_session_token("").is_err());
        assert!(validate_session_token(&"x".repeat(513)).is_err());
    }

    #[test]
    fn desktop_smoke_instance_id_accepts_only_checker_namespace() {
        std::env::set_var(
            "ASTER_DESKTOP_SMOKE_INSTANCE_ID",
            "com.workstation.personalai.desktop_smoke.production_binary",
        );
        assert!(desktop_smoke_instance_id().is_some());

        std::env::set_var(
            "ASTER_DESKTOP_SMOKE_INSTANCE_ID",
            "com.attacker.arbitrary_instance",
        );
        assert!(desktop_smoke_instance_id().is_none());
    }
}
