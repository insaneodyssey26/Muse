//! MixtapesBridge - Native Windows SMTC bridge
//!
//! Communicates with the Mixtapes Python app via stdin/stdout JSON messages.
//! Uses GetForWindow() with a hidden HWND so Windows resolves the app identity
//! from this process's exe metadata (set via rcedit).
//!
//! Protocol (stdin, one JSON per line):
//!   {"cmd": "update_status", "status": "playing"|"paused"|"stopped"|"loading"}
//!   {"cmd": "update_metadata", "title": "...", "artist": "...", "thumbnail": "https://..."}
//!   {"cmd": "update_timeline", "position": 1.5, "duration": 200.0}
//!   {"cmd": "update_controls", "can_next": true, "can_previous": false}
//!   {"cmd": "quit"}
//!
//! Protocol (stdout, one JSON per line):
//!   {"event": "button", "button": "play"|"pause"|"next"|"previous"|"stop"}

use serde::{Deserialize, Serialize};
use std::io::{self, BufRead, Write};
use std::sync::{Arc, Mutex};
use std::thread;
use windows::Foundation::Uri;
use windows::Media::{
    MediaPlaybackStatus, MediaPlaybackType, SystemMediaTransportControls,
    SystemMediaTransportControlsButton, SystemMediaTransportControlsButtonPressedEventArgs,
    SystemMediaTransportControlsTimelineProperties,
};
use windows::Storage::Streams::RandomAccessStreamReference;
use windows::Win32::System::WinRT::ISystemMediaTransportControlsInterop;

#[derive(Deserialize)]
struct Command {
    cmd: String,
    #[serde(default)]
    status: Option<String>,
    #[serde(default)]
    title: Option<String>,
    #[serde(default)]
    artist: Option<String>,
    #[serde(default)]
    thumbnail: Option<String>,
    #[serde(default)]
    position: Option<f64>,
    #[serde(default)]
    duration: Option<f64>,
    #[serde(default)]
    can_next: Option<bool>,
    #[serde(default)]
    can_previous: Option<bool>,
}

#[derive(Serialize)]
struct Event {
    event: String,
    button: String,
}

fn send_event(button: &str) {
    let event = Event {
        event: "button".to_string(),
        button: button.to_string(),
    };
    if let Ok(json) = serde_json::to_string(&event) {
        let stdout = io::stdout();
        let mut handle = stdout.lock();
        let _ = writeln!(handle, "{}", json);
        let _ = handle.flush();
    }
}

/// Create a hidden window and get SMTC via GetForWindow (like Firefox does).
/// This makes Windows resolve the app name from our exe's VersionInfo metadata.
fn setup_smtc() -> windows::core::Result<SystemMediaTransportControls> {
    unsafe {
        // Set AppUserModelID
        windows_sys::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID(
            windows::core::w!("com.pocoguy.Muse").as_ptr(),
        );

        // Register a minimal window class
        let class_name = windows::core::w!("MixtapesBridgeClass");
        let hinstance = windows_sys::Win32::System::LibraryLoader::GetModuleHandleW(
            std::ptr::null(),
        );

        let wc = windows_sys::Win32::UI::WindowsAndMessaging::WNDCLASSW {
            lpfnWndProc: Some(windows_sys::Win32::UI::WindowsAndMessaging::DefWindowProcW),
            hInstance: hinstance,
            lpszClassName: class_name.as_ptr(),
            style: 0,
            cbClsExtra: 0,
            cbWndExtra: 0,
            hIcon: std::ptr::null_mut(),
            hCursor: std::ptr::null_mut(),
            hbrBackground: std::ptr::null_mut(),
            lpszMenuName: std::ptr::null(),
        };
        windows_sys::Win32::UI::WindowsAndMessaging::RegisterClassW(&wc);

        // Create a hidden message-only window
        let hwnd = windows_sys::Win32::UI::WindowsAndMessaging::CreateWindowExW(
            0,
            class_name.as_ptr(),
            windows::core::w!("Mixtapes").as_ptr(),
            0, // no style (hidden)
            0, 0, 0, 0,
            // HWND_MESSAGE makes it a message-only window (invisible)
            -3isize as *mut _, // HWND_MESSAGE
            std::ptr::null_mut(),
            hinstance,
            std::ptr::null(),
        );

        if hwnd.is_null() {
            return Err(windows::core::Error::from_win32());
        }

        // Get SMTC via the interop interface bound to our HWND
        let interop: ISystemMediaTransportControlsInterop =
            windows::core::factory::<
                SystemMediaTransportControls,
                ISystemMediaTransportControlsInterop,
            >()?;

        let smtc: SystemMediaTransportControls =
            interop.GetForWindow(windows::Win32::Foundation::HWND(hwnd as *mut _))?;

        // Enable controls
        smtc.SetIsEnabled(true)?;
        smtc.SetIsPlayEnabled(true)?;
        smtc.SetIsPauseEnabled(true)?;
        smtc.SetIsNextEnabled(true)?;
        smtc.SetIsPreviousEnabled(true)?;
        smtc.SetIsStopEnabled(true)?;
        smtc.SetPlaybackStatus(MediaPlaybackStatus::Closed)?;

        // Handle button presses
        smtc.ButtonPressed(
            &windows::Foundation::TypedEventHandler::<
                SystemMediaTransportControls,
                SystemMediaTransportControlsButtonPressedEventArgs,
            >::new(|_, args| {
                let button = args.as_ref().unwrap().Button()?;
                let name = match button {
                    SystemMediaTransportControlsButton::Play => "play",
                    SystemMediaTransportControlsButton::Pause => "pause",
                    SystemMediaTransportControlsButton::Next => "next",
                    SystemMediaTransportControlsButton::Previous => "previous",
                    SystemMediaTransportControlsButton::Stop => "stop",
                    _ => return Ok(()),
                };
                send_event(name);
                Ok(())
            }),
        )?;

        Ok(smtc)
    }
}

fn handle_command(
    smtc: &SystemMediaTransportControls,
    cmd: &Command,
) -> windows::core::Result<()> {
    match cmd.cmd.as_str() {
        "update_status" => {
            if let Some(status) = &cmd.status {
                let s = match status.as_str() {
                    "playing" => MediaPlaybackStatus::Playing,
                    "paused" => MediaPlaybackStatus::Paused,
                    "stopped" => MediaPlaybackStatus::Stopped,
                    "loading" => MediaPlaybackStatus::Changing,
                    _ => MediaPlaybackStatus::Stopped,
                };
                smtc.SetPlaybackStatus(s)?;
            }
        }
        "update_metadata" => {
            let updater = smtc.DisplayUpdater()?;
            updater.SetType(MediaPlaybackType::Music)?;

            let props = updater.MusicProperties()?;
            if let Some(title) = &cmd.title {
                props.SetTitle(&windows::core::HSTRING::from(title.as_str()))?;
            }
            if let Some(artist) = &cmd.artist {
                props.SetArtist(&windows::core::HSTRING::from(artist.as_str()))?;
            }
            if let Some(thumb_url) = &cmd.thumbnail {
                if thumb_url.starts_with("http") {
                    if let Ok(uri) =
                        Uri::CreateUri(&windows::core::HSTRING::from(thumb_url.as_str()))
                    {
                        if let Ok(stream_ref) = RandomAccessStreamReference::CreateFromUri(&uri) {
                            let _ = updater.SetThumbnail(&stream_ref);
                        }
                    }
                }
            }
            updater.Update()?;
        }
        "update_timeline" => {
            let pos = cmd.position.unwrap_or(0.0);
            let dur = cmd.duration.unwrap_or(0.0);
            if dur > 0.0 {
                let props = SystemMediaTransportControlsTimelineProperties::new()?;
                let to_ticks = |secs: f64| -> windows::Foundation::TimeSpan {
                    windows::Foundation::TimeSpan {
                        Duration: (secs * 10_000_000.0) as i64,
                    }
                };
                props.SetStartTime(to_ticks(0.0))?;
                props.SetMinSeekTime(to_ticks(0.0))?;
                props.SetPosition(to_ticks(pos))?;
                props.SetMaxSeekTime(to_ticks(dur))?;
                props.SetEndTime(to_ticks(dur))?;
                smtc.UpdateTimelineProperties(&props)?;
            }
        }
        "update_controls" => {
            if let Some(can_next) = cmd.can_next {
                smtc.SetIsNextEnabled(can_next)?;
            }
            if let Some(can_prev) = cmd.can_previous {
                smtc.SetIsPreviousEnabled(can_prev)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn main() {
    // Hide console window
    unsafe {
        let console = windows_sys::Win32::System::Console::GetConsoleWindow();
        if !console.is_null() {
            windows_sys::Win32::UI::WindowsAndMessaging::ShowWindow(console, 0);
        }
    }

    // Signal readiness
    let stdout = io::stdout();
    {
        let mut handle = stdout.lock();
        let _ = writeln!(handle, r#"{{"event":"ready"}}"#);
        let _ = handle.flush();
    }

    let smtc = match setup_smtc() {
        Ok(s) => s,
        Err(e) => {
            let mut handle = stdout.lock();
            let _ = writeln!(
                handle,
                r#"{{"event":"error","message":"SMTC init failed: {}"}}"#,
                e
            );
            let _ = handle.flush();
            return;
        }
    };

    {
        let mut handle = stdout.lock();
        let _ = writeln!(handle, r#"{{"event":"smtc_ready"}}"#);
        let _ = handle.flush();
    }

    let smtc = Arc::new(smtc);
    let smtc_clone = Arc::clone(&smtc);

    // Read stdin on a background thread, dispatch commands to SMTC
    let quit_flag = Arc::new(Mutex::new(false));
    let quit_clone = Arc::clone(&quit_flag);

    thread::spawn(move || {
        let stdin = io::stdin();
        for line in stdin.lock().lines() {
            match line {
                Ok(line) => {
                    let line = line.trim().to_string();
                    if line.is_empty() {
                        continue;
                    }
                    match serde_json::from_str::<Command>(&line) {
                        Ok(cmd) => {
                            if cmd.cmd == "quit" {
                                *quit_clone.lock().unwrap() = true;
                                // Post a quit message to break the message loop
                                unsafe {
                                    windows_sys::Win32::UI::WindowsAndMessaging::PostQuitMessage(0);
                                }
                                break;
                            }
                            if let Err(e) = handle_command(&smtc_clone, &cmd) {
                                eprintln!("SMTC command error: {}", e);
                            }
                        }
                        Err(e) => {
                            eprintln!("JSON parse error: {}", e);
                        }
                    }
                }
                Err(_) => {
                    // stdin closed — parent process died
                    unsafe {
                        windows_sys::Win32::UI::WindowsAndMessaging::PostQuitMessage(0);
                    }
                    break;
                }
            }
        }
    });

    // Run Windows message pump on main thread (required for SMTC events)
    unsafe {
        let mut msg: windows_sys::Win32::UI::WindowsAndMessaging::MSG = std::mem::zeroed();
        while windows_sys::Win32::UI::WindowsAndMessaging::GetMessageW(
            &mut msg,
            std::ptr::null_mut(),
            0,
            0,
        ) > 0
        {
            windows_sys::Win32::UI::WindowsAndMessaging::TranslateMessage(&msg);
            windows_sys::Win32::UI::WindowsAndMessaging::DispatchMessageW(&msg);
        }
    }

    drop(smtc);
}
