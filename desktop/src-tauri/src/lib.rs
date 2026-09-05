mod engine;
use tauri_plugin_opener::OpenerExt;

#[tauri::command]
fn open_dashboard(app: tauri::AppHandle) -> Result<(), String> {
    app.opener().open_url("http://localhost:8090/", None::<&str>).map_err(|e| e.to_string())
}

use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager,
};

#[cfg(target_os = "windows")]
use window_vibrancy::apply_mica;

fn toggle_main(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        if w.is_visible().unwrap_or(false) {
            let _ = w.hide();
        } else {
            let _ = w.show();
            let _ = w.set_focus();
        }
    }
}

// tauri-plugin-window-state restaura a última posição salva sem checar se ela
// ainda cai dentro de algum monitor conectado agora. Quem já usou um monitor
// externo (ou trocou de máquina levando o estado salvo, como neste projeto que
// vive numa pasta de Drive compartilhado) acaba com a janela "aberta" fora de
// qualquer tela — ela existe, mas não há como vê-la nem arrastá-la de volta,
// já que a janela não tem barra de título (decorations: false). Por isso, logo
// depois que a janela é criada/restaurada, confirmamos que ela realmente
// sobrepõe algum monitor disponível; se não sobrepõe nenhum, recentralizamos.
fn ensure_window_on_screen(window: &tauri::WebviewWindow) {
    let Ok(monitors) = window.available_monitors() else {
        log::warn!("ensure_window_on_screen: available_monitors() falhou");
        return;
    };
    let Ok(pos) = window.outer_position() else {
        log::warn!("ensure_window_on_screen: outer_position() falhou");
        return;
    };
    let Ok(size) = window.outer_size() else {
        log::warn!("ensure_window_on_screen: outer_size() falhou");
        return;
    };

    let on_screen = monitors.iter().any(|m| {
        let mpos = m.position();
        let msize = m.size();
        pos.x < mpos.x + msize.width as i32
            && pos.x + size.width as i32 > mpos.x
            && pos.y < mpos.y + msize.height as i32
            && pos.y + size.height as i32 > mpos.y
    });

    log::info!(
        "ensure_window_on_screen: pos=({},{}) size=({},{}) monitors={} on_screen={}",
        pos.x, pos.y, size.width, size.height, monitors.len(), on_screen
    );

    if !on_screen {
        let _ = window.center();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(engine::Engine::default())
        .invoke_handler(tauri::generate_handler![engine::start_engine, open_dashboard])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // Janela transparente: no macOS o "vidro" vem do backdrop-filter (CSS)
            // sobre o desktop. No Windows aplicamos Mica nativo (Win11); no Linux
            // fica o tint translúcido do CSS, sem blur (não há material universal).
            #[cfg(target_os = "windows")]
            {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = apply_mica(&w, Some(true)); // variante escura
                }
            }

            if let Some(w) = app.get_webview_window("main") {
                ensure_window_on_screen(&w);
                // tauri-plugin-window-state pode aplicar a posicao salva
                // depois deste setup() (ordem de inicializacao nao garantida
                // entre plugins e o hook do usuario) -- reconfere pouco depois
                // pra pegar o caso em que a restauracao acontece por ultimo.
                let w2 = w.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(400));
                    ensure_window_on_screen(&w2);
                });
            }

            // ---- Ícone na barra de menu ----
            let toggle_i = MenuItem::with_id(app, "toggle", "Mostrar / Ocultar", true, None::<&str>)?;
            let on_top_i = CheckMenuItem::with_id(
                app,
                "on_top",
                "Sempre no topo",
                true,
                true,
                None::<&str>,
            )?;
            let dashboard_i = MenuItem::with_id(app, "dashboard", "Abrir painel completo ↗", true, None::<&str>)?;
            let sep = PredefinedMenuItem::separator(app)?;
            let quit_i = MenuItem::with_id(app, "quit", "Sair", true, Some("Cmd+Q"))?;
            let menu = Menu::with_items(app, &[&toggle_i, &dashboard_i, &on_top_i, &sep, &quit_i])?;

            let on_top_handle = on_top_i.clone();
            let _tray = TrayIconBuilder::with_id("main-tray")
                .icon(if cfg!(target_os = "macos") { tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))? } else { app.default_window_icon().unwrap().clone() })
                .icon_as_template(cfg!(target_os = "macos"))
                .tooltip("Consumo de IA")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "toggle" => toggle_main(app),
                    "dashboard" => { let _ = open_dashboard(app.clone()); },
                    "on_top" => {
                        let next = on_top_handle.is_checked().unwrap_or(true);
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.set_always_on_top(next);
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        toggle_main(tray.app_handle());
                    }
                })
                .build(app)?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) { app.state::<engine::Engine>().stop(); }
        });
}
