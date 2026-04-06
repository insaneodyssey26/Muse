//! MixtapesBridge - Native Windows SMTC bridge
//!
//! Communicates with the Mixtapes Python app via stdin/stdout JSON messages.
//! Provides System Media Transport Controls (SMTC) integration.
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
use windows::Foundation::Uri;
use windows::Media::Playback::MediaPlayer;
use windows::Media::{
    MediaPlaybackStatus, MediaPlaybackType, SystemMediaTransportControls,
    SystemMediaTransportControlsButton, SystemMediaTransportControlsButtonPressedEventArgs,
    SystemMediaTransportControlsTimelineProperties,
};
use windows::Storage::Streams::RandomAccessStreamReference;

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

fn setup_smtc() -> windows::core::Result<(MediaPlayer, SystemMediaTransportControls)> {
    // Set AppUserModelID so SMTC shows "Mixtapes" instead of "Unknown app"
    unsafe {
        windows_sys::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID(
            windows::core::w!("com.pocoguy.Muse").as_ptr(),
        );
    }

    let player = MediaPlayer::new()?;
    let smtc = player.SystemMediaTransportControls()?;

    // Disable auto-integration
    player.CommandManager()?.SetIsEnabled(false)?;

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

    Ok((player, smtc))
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
                    if let Ok(uri) = Uri::CreateUri(&windows::core::HSTRING::from(thumb_url.as_str())) {
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
    // Hide console window (bridge communicates via stdin/stdout, not a visible console)
    unsafe {
        let console = windows_sys::Win32::System::Console::GetConsoleWindow();
        if !console.is_null() {
            windows_sys::Win32::UI::WindowsAndMessaging::ShowWindow(console, 0); // SW_HIDE
        }
    }

    // Signal readiness
    let stdout = io::stdout();
    {
        let mut handle = stdout.lock();
        let _ = writeln!(handle, r#"{{"event":"ready"}}"#);
        let _ = handle.flush();
    }

    let (player, smtc) = match setup_smtc() {
        Ok(result) => result,
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

    // Set initial display info so SMTC doesn't show "Unknown app"
    {
        let updater = smtc.DisplayUpdater().unwrap();
        updater.SetType(MediaPlaybackType::Music).unwrap();
        let props = updater.MusicProperties().unwrap();
        let _ = props.SetTitle(&windows::core::HSTRING::from("Mixtapes"));
        let _ = props.SetArtist(&windows::core::HSTRING::from(""));
        let _ = updater.Update();
    }

    {
        let mut handle = stdout.lock();
        let _ = writeln!(handle, r#"{{"event":"smtc_ready"}}"#);
        let _ = handle.flush();
    }

    // Read commands from stdin
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
                            break;
                        }
                        if let Err(e) = handle_command(&smtc, &cmd) {
                            eprintln!("SMTC command error: {}", e);
                        }
                    }
                    Err(e) => {
                        eprintln!("JSON parse error: {}", e);
                    }
                }
            }
            Err(_) => break, // stdin closed
        }
    }

    // Keep player alive until we exit so SMTC doesn't disappear
    drop(smtc);
    drop(player);
}
