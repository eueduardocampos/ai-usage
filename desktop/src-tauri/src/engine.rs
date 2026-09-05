use std::{io::{Read, Write}, net::{TcpStream, SocketAddr}, process::{Child, Command, Stdio}, sync::Mutex, time::Duration};
use tauri::Manager;

#[derive(Default)]
pub struct Engine(pub Mutex<Option<Child>>);
impl Engine {
    pub fn stop(&self) {
        if let Ok(mut slot) = self.0.lock() {
            if let Some(mut child) = slot.take() { let _ = child.kill(); let _ = child.wait(); }
        }
    }
}
fn healthy() -> bool {
    let addr: SocketAddr = "127.0.0.1:8090".parse().unwrap();
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(250)) else { return false; };
    let _ = s.set_read_timeout(Some(Duration::from_millis(300)));
    let _ = s.set_write_timeout(Some(Duration::from_millis(300)));
    if s.write_all(b"GET /api/health HTTP/1.0\r\nHost: localhost\r\n\r\n").is_err() { return false; }
    let mut reply = String::new();
    if s.take(4096).read_to_string(&mut reply).is_err() { return false; }
    reply.split_once("\r\n\r\n").and_then(|(_,body)|serde_json::from_str::<serde_json::Value>(body).ok())
        .is_some_and(|v|v["app"]=="ai-usage" && v["protocol"]==1)
}
fn start(app: tauri::AppHandle) -> Result<String,String> {
    let state = app.state::<Engine>();
    let mut slot = state.0.lock().map_err(|_|"Falha ao iniciar o painel.")?;
    if healthy() { return Ok("http://localhost:8090/".into()); }
    if TcpStream::connect_timeout(&"127.0.0.1:8090".parse().unwrap(), Duration::from_millis(250)).is_ok() {
        return Err("A porta 8090 está ocupada. Feche o painel antigo ou o aplicativo que está usando essa porta e tente novamente.".into());
    }
    if let Some(mut old) = slot.take() { let _ = old.kill(); let _ = old.wait(); }
    let folder = app.path().resource_dir().map_err(|e|e.to_string())?.join("engine");
    let executable = folder.join(if cfg!(windows) {"ai-usage-engine.exe"} else {"ai-usage-engine"});
    let logs = app.path().app_log_dir().map_err(|e|e.to_string())?;
    std::fs::create_dir_all(&logs).map_err(|e|e.to_string())?;
    let log = std::fs::OpenOptions::new().create(true).append(true).open(logs.join("engine.log")).map_err(|e|e.to_string())?;
    let mut command=Command::new(executable);
    command.stdin(Stdio::null()).stdout(log.try_clone().map_err(|e|e.to_string())?).stderr(log);
    #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
    let child=command.spawn().map_err(|e|format!("Não foi possível iniciar o motor incluído no aplicativo: {e}"))?;
    *slot=Some(child);
    for _ in 0..80 {
        if healthy() { return Ok("http://localhost:8090/".into()); }
        if let Some(child)=slot.as_mut() {
            if let Ok(Some(status))=child.try_wait() { return Err(format!("O motor encerrou ({status}). Consulte engine.log em {}.",logs.display())); }
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    if let Some(mut child)=slot.take() { let _=child.kill(); let _=child.wait(); }
    Err("O painel demorou para iniciar. Tente novamente.".into())
}
#[tauri::command]
pub async fn start_engine(app: tauri::AppHandle) -> Result<String,String> {
    tauri::async_runtime::spawn_blocking(move || start(app)).await.map_err(|e|e.to_string())?
}
