mod adopt;
mod apply;
mod cli;
mod cwd_guard;
mod launch;
mod release;
mod selfupdate;
mod services;
mod slots;
mod tree;

use anyhow::Context;
use cli::Command;
use std::path::PathBuf;

fn hermes_home() -> anyhow::Result<PathBuf> {
    if let Some(path) = std::env::var_os("HERMES_HOME") {
        return Ok(PathBuf::from(path));
    }
    Ok(dirs::home_dir()
        .context("cannot find home directory")?
        .join(".hermes"))
}

fn main() -> anyhow::Result<()> {
    let cli = cli::parse();

    match cli.command {
        Some(Command::Launch { args }) => launch(args),
        Some(Command::Install { source, channel }) => install(source, channel),
        Some(Command::Apply {
            source,
            target_version,
            notify_file,
            relaunch_app,
            report,
        }) => apply(source, target_version, notify_file, relaunch_app, report),
        Some(Command::Rollback) => rollback(),
        Some(Command::Status { check, json }) => status(check, json),
        Some(Command::Adopt {
            from_checkout,
            source,
            undo,
        }) => adopt(from_checkout, source, undo),
        Some(Command::SelfRestage) => self_restage(),
        None => {
            // Should not happen — parse() fills in a default.
            unreachable!("cli::parse() should always set a command")
        }
    }
}

fn launch(args: Vec<String>) -> anyhow::Result<()> {
    launch::launch(args)
}

fn trusted_release_pubkey() -> anyhow::Result<&'static str> {
    option_env!("HERMES_RELEASE_PUBLIC_KEY")
        .filter(|key| !key.trim().is_empty())
        .map(str::trim)
        .ok_or_else(|| {
            anyhow::anyhow!("this updater was built without the Hermes release public key")
        })
}

fn release_source(source: Option<String>) -> anyhow::Result<release::ReleaseSource> {
    release::ReleaseSource::parse(
        source
            .as_deref()
            .unwrap_or("https://github.com/NousResearch/hermes-agent/releases/download"),
    )
}

fn install(source: Option<String>, channel: String) -> anyhow::Result<()> {
    let home = hermes_home()?;
    let source = release_source(source)?;
    let manifest = apply::apply_release(apply::ApplyRequest {
        hermes_home: &home,
        source: &source,
        version: None,
        channel: &channel,
        trusted_pubkey: trusted_release_pubkey()?,
    })?;
    let _marker = apply::UpdateMarker::acquire(&home)?;
    apply::activate_stable_launchers(&home, &manifest.version)?;
    println!("Installed Hermes {}", manifest.version);
    Ok(())
}

fn apply(
    source: Option<String>,
    version: Option<String>,
    notify_file: Option<String>,
    relaunch_app: Option<String>,
    _report: String,
) -> anyhow::Result<()> {
    let home = hermes_home()?;
    let source = release_source(source)?;
    let manifest = apply::apply_release(apply::ApplyRequest {
        hermes_home: &home,
        source: &source,
        version: version.as_deref(),
        channel: "stable",
        trusted_pubkey: trusted_release_pubkey()?,
    })?;
    let _marker = apply::UpdateMarker::acquire(&home)?;
    apply::activate_stable_launchers(&home, &manifest.version)?;
    if let Err(error) = apply::apply_feature_ledger(&home, &manifest.version) {
        eprintln!("warning: feature ledger application failed: {error:#}");
    }
    if let Err(error) = services::restart_gateway(&home) {
        eprintln!("warning: gateway restart failed: {error:#}");
    }
    services::write_notify_files(
        &home,
        0,
        &format!("Updated Hermes to {}", manifest.version),
        notify_file.as_deref(),
    )?;
    if let Some(executable) = relaunch_app {
        std::process::Command::new(executable).spawn()?;
    }
    println!("Updated Hermes to {}", manifest.version);
    Ok(())
}

fn rollback() -> anyhow::Result<()> {
    let hermes_home = hermes_home()?;
    let version = slots::rollback(&hermes_home)?;
    println!("Rolled back to {}", version);
    Ok(())
}

fn status(check: bool, json: bool) -> anyhow::Result<()> {
    let hermes_home = hermes_home()?;
    let current = slots::resolve_current(&hermes_home).unwrap_or(None);
    let previous = slots::resolve_previous(&hermes_home).unwrap_or(None);

    if json {
        let status = serde_json::json!({
            "current": current,
            "previous": previous,
            "check": check,
        });
        println!("{}", serde_json::to_string_pretty(&status).unwrap());
    } else {
        println!("hermes-updater 0.1.0");
        match current {
            Some(v) => println!("  current:  {}", v),
            None => println!("  current:  (none)"),
        }
        match previous {
            Some(v) => println!("  previous: {}", v),
            None => println!("  previous: (none)"),
        }
    }
    Ok(())
}

fn adopt(from_checkout: Option<String>, source: Option<String>, undo: bool) -> anyhow::Result<()> {
    let hermes_home = hermes_home()?;

    let checkout = match from_checkout {
        Some(path) => std::path::PathBuf::from(path),
        None => {
            // Default to the current checkout (PROJECT_ROOT)
            std::path::PathBuf::from(".")
        }
    };

    let trusted_pubkey = if undo { "" } else { trusted_release_pubkey()? };
    adopt::adopt(
        &hermes_home,
        &checkout,
        source.as_deref(),
        undo,
        trusted_pubkey,
    )
}

fn self_restage() -> anyhow::Result<()> {
    let home = hermes_home()?;
    let version =
        slots::resolve_current(&home)?.ok_or_else(|| anyhow::anyhow!("no current managed slot"))?;
    apply::activate_stable_launchers(&home, &version)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;

    struct EnvRestore {
        key: &'static str,
        value: Option<OsString>,
    }

    impl Drop for EnvRestore {
        fn drop(&mut self) {
            match &self.value {
                Some(value) => std::env::set_var(self.key, value),
                None => std::env::remove_var(self.key),
            }
        }
    }

    #[test]
    fn hermes_home_honors_environment_override() {
        let _restore = EnvRestore {
            key: "HERMES_HOME",
            value: std::env::var_os("HERMES_HOME"),
        };
        let temp = tempfile::tempdir().unwrap();
        std::env::set_var("HERMES_HOME", temp.path());

        assert_eq!(hermes_home().unwrap(), temp.path());
    }

    #[test]
    fn test_status_works() {
        // status is the one verb that isn't a stub — it prints a version line.
        assert!(status(false, false).is_ok());
    }
}
