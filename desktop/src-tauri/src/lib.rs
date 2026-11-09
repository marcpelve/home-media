use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::process::current_binary;
use tauri::{App, Manager, WindowEvent};

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app: &mut App| {
            // Tauri v2 has no sidecar Command API: tauri-build copies the
            // externalBin sidecar next to the built exe, stripping the
            // target-triple suffix, so spawn it by base name.
            let exe = current_binary(&app.env())?;
            let sidecar_name = if cfg!(target_os = "windows") {
                "home-media.exe".to_string()
            } else {
                "home-media".to_string()
            };
            let sidecar = exe
                .parent()
                .expect("executable has a parent directory")
                .join(sidecar_name);
            let child = Command::new(sidecar).spawn()?;
            app.manage(Mutex::new(Some(child)));
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                if let Some(state) = window.app_handle().try_state::<Mutex<Option<Child>>>() {
                    if let Some(mut child) = state.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
