fn main() {
    let target = std::env::var("CARGO_BIN_NAME").unwrap_or_default();

    match target.as_str() {
        "Mixtapes" => {
            // Launcher gets icon + version info
            if std::path::Path::new("launcher.rc").exists() {
                let _ = embed_resource::compile("launcher.rc", embed_resource::NONE);
            }
        }
        "MixtapesBridge" => {
            // Bridge gets version info (ProductName = "Mixtapes" for SMTC identity)
            if std::path::Path::new("bridge.rc").exists() {
                let _ = embed_resource::compile("bridge.rc", embed_resource::NONE);
            }
        }
        _ => {}
    }
}
