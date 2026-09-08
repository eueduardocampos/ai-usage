import AppKit
import Combine
import Foundation
import ServiceManagement

/// Preferências do usuário, persistidas em UserDefaults. Guarda as CHAVES das
/// quotas escolhidas (`claude:five_hour`, `codex:codex_bengalfox:secondary`...)
/// e não índices: o backend pode passar a mandar janelas novas (foi o caso do
/// Fable) sem bagunçar o que já estava marcado.
/// Formatos do widget. Cada um guarda posição e tamanho próprios (autosave
/// separado), então alternar não bagunça o que já estava ajustado.
enum WidgetLayout: String, CaseIterable, Identifiable {
    case padrao, vertical, compacto

    var id: String { rawValue }

    var title: String {
        switch self {
        case .padrao: return "Padrão"
        case .vertical: return "Vertical"
        case .compacto: return "Compacto"
        }
    }

    var detail: String {
        switch self {
        case .padrao: return "Claude e Codex lado a lado, com projeção e ritmo"
        case .vertical: return "Cartões empilhados, para encostar na lateral da tela"
        case .compacto: return "Uma linha por cota, sem rodapé"
        }
    }

    var defaultSize: NSSize {
        switch self {
        case .padrao: return NSSize(width: 640, height: 440)
        case .vertical: return NSSize(width: 380, height: 660)
        case .compacto: return NSSize(width: 320, height: 250)
        }
    }

    var minSize: NSSize {
        switch self {
        case .padrao: return NSSize(width: 460, height: 400)
        case .vertical: return NSSize(width: 300, height: 420)
        case .compacto: return NSSize(width: 260, height: 170)
        }
    }

    var autosaveName: String { "AIUsageWidgetPanel.\(rawValue)" }
}

@MainActor
final class AppPreferences: ObservableObject {
    private enum Keys {
        static let menuBarQuotas = "menuBar.quotaKeys"
        static let showLabels = "menuBar.showLabels"
        static let layout = "widget.layout"
        static let hideIdle = "widget.hideIdleQuotas"
    }

    /// Trocar o formato redimensiona a janela; quem escuta é o AppDelegate.
    var onLayoutChange: ((WidgetLayout) -> Void)?

    @Published var layout: WidgetLayout {
        didSet {
            UserDefaults.standard.set(layout.rawValue, forKey: Keys.layout)
            onLayoutChange?(layout)
        }
    }

    /// Chamado quando algo muda, pra redesenhar a barra na hora (sem esperar
    /// o próximo ciclo de 10s).
    var onChange: (() -> Void)?

    @Published var menuBarQuotaKeys: Set<String> {
        didSet { save(); onChange?() }
    }

    /// Com rótulo: "5h 15% · 7d 20%". Sem: "15% · 20%" (ocupa menos barra).
    @Published var showLabels: Bool {
        didSet { save(); onChange?() }
    }

    /// Esconde cotas em 0% no widget (semana sem Codex, modelo não usado).
    @Published var hideIdleQuotas: Bool {
        didSet { save(); onChange?() }
    }

    init() {
        let d = UserDefaults.standard
        layout = WidgetLayout(rawValue: d.string(forKey: Keys.layout) ?? "") ?? .padrao
        if let saved = d.array(forKey: Keys.menuBarQuotas) as? [String] {
            menuBarQuotaKeys = Set(saved)
        } else {
            // Padrão: as duas janelas gerais do Claude, que é o que aperta.
            menuBarQuotaKeys = ["claude:five_hour", "claude:seven_day"]
        }
        showLabels = d.object(forKey: Keys.showLabels) as? Bool ?? true
        hideIdleQuotas = d.object(forKey: Keys.hideIdle) as? Bool ?? false
    }

    private func save() {
        let d = UserDefaults.standard
        d.set(Array(menuBarQuotaKeys), forKey: Keys.menuBarQuotas)
        d.set(showLabels, forKey: Keys.showLabels)
        d.set(hideIdleQuotas, forKey: Keys.hideIdle)
    }

    // MARK: - Abrir ao iniciar sessão

    /// Só funciona a partir de um .app instalado (SMAppService precisa de um
    /// bundle registrado); rodando via `swift run` não há o que registrar.
    var canManageLoginItem: Bool {
        Bundle.main.bundleIdentifier != nil && Bundle.main.bundleURL.pathExtension == "app"
    }

    var opensAtLogin: Bool {
        SMAppService.mainApp.status == .enabled
    }

    func setOpensAtLogin(_ enabled: Bool) throws {
        if enabled {
            try SMAppService.mainApp.register()
        } else {
            try SMAppService.mainApp.unregister()
        }
        objectWillChange.send()
    }
}
