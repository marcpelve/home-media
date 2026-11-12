use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{App, Manager, WindowEvent};
use tauri_plugin_updater::UpdaterExt;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

/// Update info surfaced to the frontend.
#[derive(serde::Serialize)]
struct UpdateInfo {
    version: String,
    notes: Option<String>,
}

/// Ask the configured update endpoint(s) whether a newer release is available.
#[tauri::command]
async fn check_for_update(app: tauri::AppHandle) -> Result<Option<UpdateInfo>, String> {
    let updater = app.updater().map_err(|e| e.to_string())?;
    match updater.check().await.map_err(|e| e.to_string())? {
        Some(update) => Ok(Some(UpdateInfo {
            version: update.version,
            notes: update.body,
        })),
        None => Ok(None),
    }
}

/// Download and install the latest release, then relaunch.
#[tauri::command]
async fn install_update(app: tauri::AppHandle) -> Result<(), String> {
    let updater = app.updater().map_err(|e| e.to_string())?;
    let update = updater
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "No update available".to_string())?;
    update
        .download_and_install(
            |_received: usize, _total: Option<u64>| {},
            || {},
        )
        .await
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Return the app version string.
#[tauri::command]
fn app_get_version(app: tauri::AppHandle) -> Result<String, String> {
    Ok(app.package_info().version.to_string())
}

/// Open a URL in the default browser.
#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    open::that(&url).map_err(|e| e.to_string())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![check_for_update, install_update, app_get_version, open_url])
        .setup(|app: &mut App| {
            // If a backend is already serving on :8790 (a previous instance
            // whose sidecar did not clean up), reuse it. Spawning a second
            // sidecar would fail to bind the port and crash, leaving the
            // frontend with no backend to reach. We don't own a reused
            // backend, so store None (the on-close kill is then a no-op).
            let backend_up = std::net::TcpStream::connect_timeout(
                &"127.0.0.1:8790".parse().unwrap(),
                std::time::Duration::from_millis(750),
            )
            .is_ok();
            if backend_up {
                app.manage(Mutex::new(None::<Child>));
                return Ok(());
            }
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
            let mut cmd = Command::new(&sidecar);
            // Null out std handles so Windows doesn't allocate a console/ConPTY.
            cmd.stdin(std::process::Stdio::null());
            cmd.stdout(std::process::Stdio::null());
            cmd.stderr(std::process::Stdio::null());
            #[cfg(target_os = "windows")]
            {
                cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
            }
            let child = cmd.spawn()?;
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
