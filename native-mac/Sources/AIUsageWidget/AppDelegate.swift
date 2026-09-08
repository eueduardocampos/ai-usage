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
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var panel: NSPanel!
    private var alwaysOnTopItem: NSMenuItem!
    private let store = AppStateStore()

    // Mais alto que a v1: 3 quotas por cartão agora (5h/7d/7d-Sonnet no
    // Claude, Codex/Spark-5h/Spark-7d no Codex), não mais 2.
    private let defaultSize = NSSize(width: 640, height: 440)
    private let expandedSize = NSSize(width: 900, height: 610)
    private var isExpanded = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        setupStatusItem()
        setupPanel()
        store.startPolling()
        panel.makeKeyAndOrderFront(nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        store.stopPolling()
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(
            systemSymbolName: "gauge.with.dots.needle.67percent",
            accessibilityDescription: "AI Usage"
        )

        let menu = NSMenu()

        let toggleItem = NSMenuItem(title: "Mostrar/Ocultar", action: #selector(togglePanel), keyEquivalent: "")
        toggleItem.target = self
        menu.addItem(toggleItem)

        let dashboardItem = NSMenuItem(title: "Abrir painel completo", action: #selector(openDashboard), keyEquivalent: "")
        dashboardItem.target = self
        menu.addItem(dashboardItem)

        alwaysOnTopItem = NSMenuItem(title: "Sempre no topo", action: #selector(toggleAlwaysOnTop), keyEquivalent: "")
        alwaysOnTopItem.target = self
        alwaysOnTopItem.state = .on
        menu.addItem(alwaysOnTopItem)

        menu.addItem(.separator())

        let quitItem = NSMenuItem(title: "Sair", action: #selector(quit), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        statusItem.menu = menu
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
        panel.center()

        self.panel = panel
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
