import Combine
import Foundation
import ServiceManagement

/// Preferências do usuário, persistidas em UserDefaults. Guarda as CHAVES das
/// quotas escolhidas (`claude:five_hour`, `codex:codex_bengalfox:secondary`...)
/// e não índices: o backend pode passar a mandar janelas novas (foi o caso do
/// Fable) sem bagunçar o que já estava marcado.
@MainActor
final class AppPreferences: ObservableObject {
    private enum Keys {
        static let menuBarQuotas = "menuBar.quotaKeys"
        static let showLabels = "menuBar.showLabels"
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

    init() {
        let d = UserDefaults.standard
        if let saved = d.array(forKey: Keys.menuBarQuotas) as? [String] {
            menuBarQuotaKeys = Set(saved)
        } else {
            // Padrão: as duas janelas gerais do Claude, que é o que aperta.
            menuBarQuotaKeys = ["claude:five_hour", "claude:seven_day"]
        }
        showLabels = d.object(forKey: Keys.showLabels) as? Bool ?? true
    }

    private func save() {
        let d = UserDefaults.standard
        d.set(Array(menuBarQuotaKeys), forKey: Keys.menuBarQuotas)
        d.set(showLabels, forKey: Keys.showLabels)
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
