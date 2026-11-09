use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{App, Manager, WindowEvent};

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app: &mut App| {
            // The backend ships as a PyInstaller one-dir bundle in the app's
            // resources: a home-media/ folder (exe + _internal/), no runtime
            // extraction, so it starts in a couple of seconds. Tauri may place
            // bundle resources directly under the resource dir or in a
            // resources/ subfolder, so try both locations.
            let res = app.path().resource_dir()?;
            let sidecar_name = if cfg!(target_os = "windows") {
                "home-media.exe".to_string()
            } else {
                "home-media".to_string()
            };
            let candidates = [
                res.join("home-media").join(&sidecar_name),
                res.join("resources").join("home-media").join(&sidecar_name),
            ];
            let sidecar = candidates
                .iter()
                .find(|p| p.exists())
                .ok_or_else(|| {
                    std::io::Error::new(std::io::ErrorKind::NotFound, "home-media sidecar not found")
                })?
                .clone();
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
