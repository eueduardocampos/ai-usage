import AppKit
import SwiftUI

/// Monta o item da barra de menu e o painel flutuante -- equivalente nativo
/// ao tray + janela do app Tauri (desktop/src-tauri/src/lib.rs), mas sem
/// gerenciar o motor Python: este app só consome localhost:8090.
///
/// A janela é totalmente sem moldura (sem barra de título do sistema) --
/// os "semáforos" de fechar/minimizar/zoom são desenhados dentro do próprio
/// card SwiftUI (ver WidgetPanelView), não numa tarja separada por cima.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var panel: NSPanel!
    private var alwaysOnTopItem: NSMenuItem!
    private var statusMessageItem: NSMenuItem!
    private var refreshItem: NSMenuItem!
    private var updateItem: NSMenuItem!
    private var layoutMenuItems: [NSMenuItem] = []
    private var preferencesWindow: NSWindow?
    private let store = AppStateStore()
    private let prefs = AppPreferences()

    // Mais alto que a v1: 3 quotas por cartão agora (5h/7d/7d-Sonnet no
    // Claude, Codex/Spark-5h/Spark-7d no Codex), não mais 2.
    private let defaultSize = NSSize(width: 640, height: 440)
    private let expandedSize = NSSize(width: 900, height: 610)
    private var isExpanded = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        store.onUpdate = { [weak self] in self?.updateStatusItem() }
        prefs.onChange = { [weak self] in self?.updateStatusItem() }
        prefs.onLayoutChange = { [weak self] layout in self?.applyLayout(layout) }
        setupStatusItem()
        setupPanel()
        store.startPolling()
        panel.makeKeyAndOrderFront(nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        store.stopPolling()
    }

    private func setupStatusItem() {
        // variableLength: o item cresce conforme o texto ("5h 15% · 7d 20%").
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.imagePosition = .imageLeading

        let menu = NSMenu()
        menu.delegate = self

        statusMessageItem = NSMenuItem(title: "Carregando…", action: nil, keyEquivalent: "")
        statusMessageItem.isEnabled = false
        menu.addItem(statusMessageItem)
        menu.addItem(.separator())

        let toggleItem = NSMenuItem(title: "Mostrar/Ocultar widget", action: #selector(togglePanel), keyEquivalent: "")
        toggleItem.target = self
        menu.addItem(toggleItem)

        let dashboardItem = NSMenuItem(title: "Abrir painel completo", action: #selector(openDashboard), keyEquivalent: "")
        dashboardItem.target = self
        menu.addItem(dashboardItem)

        alwaysOnTopItem = NSMenuItem(title: "Sempre no topo", action: #selector(toggleAlwaysOnTop), keyEquivalent: "")
        alwaysOnTopItem.target = self
        alwaysOnTopItem.state = .on
        menu.addItem(alwaysOnTopItem)

        let layoutItem = NSMenuItem(title: "Formato", action: nil, keyEquivalent: "")
        let layoutMenu = NSMenu()
        for layout in WidgetLayout.allCases {
            let item = NSMenuItem(title: layout.title, action: #selector(changeLayout(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = layout.rawValue
            item.toolTip = layout.detail
            layoutMenu.addItem(item)
        }
        layoutItem.submenu = layoutMenu
        menu.addItem(layoutItem)
        layoutMenuItems = layoutMenu.items

        menu.addItem(.separator())

        refreshItem = NSMenuItem(title: "Atualizar agora", action: #selector(refreshNow), keyEquivalent: "r")
        refreshItem.target = self
        menu.addItem(refreshItem)

        let reconnectItem = NSMenuItem(title: "Reconectar conta Claude", action: #selector(reconnect), keyEquivalent: "")
        reconnectItem.target = self
        menu.addItem(reconnectItem)

        let prefsItem = NSMenuItem(title: "Preferências…", action: #selector(showPreferences), keyEquivalent: ",")
        prefsItem.target = self
        menu.addItem(prefsItem)

        menu.addItem(.separator())

        // Só aparece quando há versão nova (o backend consulta as releases).
        updateItem = NSMenuItem(title: "", action: #selector(openRelease), keyEquivalent: "")
        updateItem.target = self
        updateItem.isHidden = true
        menu.addItem(updateItem)

        let quitItem = NSMenuItem(title: "Sair", action: #selector(quit), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        statusItem.menu = menu
        updateStatusItem()
    }

    /// Redesenha ícone + texto da barra. Chamado a cada leitura e sempre que
    /// as preferências mudam.
    private func updateStatusItem() {
        guard let button = statusItem?.button else { return }
        let keys = prefs.menuBarQuotaKeys
        let color = store.isOffline
            ? NSColor.tertiaryLabelColor
            : NSColor(AppColors.forStatus(store.worstStatus(keys: keys)))
        let symbol = store.isOffline ? "gauge.with.dots.needle.bottom.0percent"
                                     : "gauge.with.dots.needle.67percent"
        let image = NSImage(systemSymbolName: symbol, accessibilityDescription: "AI Usage")?
            .withSymbolConfiguration(.init(pointSize: 13, weight: .regular)
                .applying(.init(paletteColors: [color])))
        image?.isTemplate = false
        button.image = image

        if store.isOffline {
            button.title = ""
        } else if let text = store.menuBarText(keys: keys, showLabels: prefs.showLabels) {
            button.attributedTitle = NSAttributedString(string: " " + text, attributes: [
                .font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .medium),
            ])
        } else {
            button.title = ""
        }

        statusMessageItem?.title = store.isOffline
            ? "Painel offline (localhost:8090)"
            : (store.switchMessage ?? store.lastUpdatedText)

        if let update = store.state?.update, update.available, let latest = update.latest {
            updateItem?.title = "Atualizar para a versão \(latest) ↗"
            updateItem?.isHidden = false
        } else {
            updateItem?.isHidden = true
        }
    }

    @objc private func changeLayout(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let layout = WidgetLayout(rawValue: raw) else { return }
        prefs.layout = layout
        if !panel.isVisible { panel.makeKeyAndOrderFront(nil) }
    }

    @objc private func openRelease() {
        guard let raw = store.state?.update?.url, let url = URL(string: raw) else { return }
        NSWorkspace.shared.open(url)
    }

    private func setupPanel() {
        let hosting = NSHostingView(
            rootView: WidgetPanelView(
                onOpenDashboard: { [weak self] in self?.openDashboard() },
                onClose: { [weak self] in self?.panel.orderOut(nil) },
                onMinimize: { [weak self] in self?.panel.miniaturize(nil) },
                onZoom: { [weak self] in self?.toggleZoom() }
            )
            .environmentObject(store)
            .environmentObject(prefs)
        )
        // Recorta a própria view de conteúdo num retângulo arredondado de
        // verdade (não só desenha um por cima) -- é o que faz o macOS
        // calcular a sombra nativa em cima do formato real da janela, em vez
        // de sobrar uma costura reta do retângulo original nas bordas.
        hosting.wantsLayer = true
        hosting.layer?.backgroundColor = .clear
        hosting.layer?.cornerRadius = 18
        hosting.layer?.cornerCurve = .continuous // "squircle" do macOS, não círculo puro
        hosting.layer?.masksToBounds = true

        let panel = NSPanel(
            contentRect: NSRect(origin: .zero, size: defaultSize),
            styleMask: [.nonactivatingPanel, .resizable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        panel.isMovableByWindowBackground = true
        panel.level = .floating
        panel.isOpaque = false
        panel.backgroundColor = .clear
        // Sombra nativa do sistema, calculada em cima do formato arredondado
        // acima -- não a desenhamos mais à mão no SwiftUI.
        panel.hasShadow = true
        panel.minSize = NSSize(width: 440, height: 420)
        panel.contentView = hosting
        self.panel = panel
        applyLayout(prefs.layout, animate: false)
    }

    /// Cada formato tem tamanho mínimo e autosave próprios: alternar entre
    /// eles não desfaz o ajuste feito no outro.
    private func applyLayout(_ layout: WidgetLayout, animate: Bool = true) {
        guard let panel else { return }
        panel.minSize = layout.minSize
        panel.setFrameAutosaveName(layout.autosaveName)
        if !panel.setFrameUsingName(layout.autosaveName) {
            // primeira vez neste formato: tamanho padrão, ancorado no canto
            // superior esquerdo de onde a janela já estava
            var frame = panel.frame
            frame.origin.y += frame.height - layout.defaultSize.height
            frame.size = layout.defaultSize
            if frame.origin == .zero { panel.center() } else {
                panel.setFrame(frame, display: true, animate: animate)
            }
        }
        panel.invalidateShadow()
    }

    // MARK: - Ações do menu

    @objc private func refreshNow() {
        refreshItem.title = "Atualizando…"
        refreshItem.isEnabled = false
        Task { [weak self] in
            guard let self else { return }
            var title = "Atualizar agora"
            do {
                let r = try await AIUsageAPI.refresh()
                if r.ok == false {
                    let motivo = r.reason == "rate_limited"
                        ? "conta no limite da API" : "consulta muito recente"
                    title = "Atualizar agora (\(motivo), \(r.retryIn ?? 0)s)"
                }
            } catch {
                title = "Atualizar agora (painel fora do ar)"
            }
            self.refreshItem.title = title
            self.refreshItem.isEnabled = true
            // devolve o título normal depois que o usuário tiver lido o motivo
            try? await Task.sleep(for: .seconds(12))
            self.refreshItem.title = "Atualizar agora"
        }
    }

    @objc private func reconnect() {
        Task {
            // O backend abre o navegador e captura o callback; o app nativo
            // não tem OAuth próprio (mesma divisão do app Tauri).
            try? await AIUsageAPI.startAuth()
        }
    }

    @objc private func showPreferences() {
        if preferencesWindow == nil {
            let hosting = NSHostingView(
                rootView: PreferencesView()
                    .environmentObject(store)
                    .environmentObject(prefs)
            )
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 380, height: 320),
                                  styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = "AI Usage · Preferências"
            window.contentView = hosting
            window.isReleasedWhenClosed = false
            window.center()
            preferencesWindow = window
        }
        NSApp.activate(ignoringOtherApps: true)
        preferencesWindow?.makeKeyAndOrderFront(nil)
    }

    /// Antes de abrir o menu, atualiza a linha de status (o veredito muda
    /// entre um poll e outro).
    func menuWillOpen(_ menu: NSMenu) {
        updateStatusItem()
        for item in layoutMenuItems {
            item.state = (item.representedObject as? String) == prefs.layout.rawValue ? .on : .off
        }
    }

    @objc private func togglePanel() {
        if panel.isVisible {
            panel.orderOut(nil)
        } else {
            panel.makeKeyAndOrderFront(nil)
        }
    }

    private func toggleZoom() {
        isExpanded.toggle()
        let size = isExpanded ? expandedSize : defaultSize
        var frame = panel.frame
        // Ancora o canto superior esquerdo e cresce pra baixo/direita, em
        // vez de crescer a partir do centro.
        frame.origin.y += frame.height - size.height
        frame.size = size
        panel.setFrame(frame, display: true, animate: true)
        panel.invalidateShadow()
    }

    @objc private func openDashboard() {
        NSWorkspace.shared.open(AIUsageAPI.baseURL)
    }

    @objc private func toggleAlwaysOnTop() {
        let isOn = alwaysOnTopItem.state == .on
        alwaysOnTopItem.state = isOn ? .off : .on
        panel.level = isOn ? .normal : .floating
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }
}
