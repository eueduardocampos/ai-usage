import Combine
import Foundation

struct QuotaData: Identifiable {
    let id = UUID()
    let label: String
    let percent: Double?
    let status: String
    let resetText: String
    let isStale: Bool
    /// Projeção pro reset: onde a cota deve chegar se o ritmo continuar. É o
    /// diferencial do painel -- o valor atual qualquer app mostra.
    let projected: Double?
    let projectedText: String?

    /// Cota sem consumo nenhum: pode ser escondida pra sobrar tela.
    var isIdle: Bool { (percent ?? 0) == 0 }
}

/// Consome GET /api/state a cada 10s (mesmo ritmo de web/src/Widget.jsx) e
/// deriva os valores já prontos pra UI. Não gerencia o motor Python -- se a
/// API não responder, só marca isOffline e segue tentando no próximo ciclo.
@MainActor
final class AppStateStore: ObservableObject {
    @Published private(set) var state: AppState?
    @Published private(set) var isOffline = false
    @Published private(set) var lastUpdated: Date?

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    var lastUpdatedText: String {
        guard let lastUpdated else { return "Sem atualização ainda" }
        return "Atualizado às \(Self.timeFormatter.string(from: lastUpdated))"
    }

    private var pollTask: Task<Void, Never>?

    /// Chamado ao fim de cada leitura (com ou sem sucesso). O item da barra de
    /// menu é AppKit puro, então precisa ser redesenhado à mão -- e ler o
    /// estado aqui, e não em `objectWillChange`, evita pegar o valor antigo.
    var onUpdate: (() -> Void)?

    func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refreshOnce()
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    private func refreshOnce() async {
        do {
            state = try await AIUsageAPI.fetchState()
            isOffline = false
            lastUpdated = Date()
        } catch {
            isOffline = true
        }
        onUpdate?()
    }

    var isLive: Bool {
        Formatting.isLive(state?.generatedAt)
    }

    private var stale: Bool {
        Formatting.isStale(state?.snapshotTs ?? state?.generatedAt)
    }

    /// Tudo vem pronto de `quotas[]` (rótulo, projeção, semáforo): o backend
    /// é o único lugar que decide o que é uma cota e como classificá-la, e
    /// Claude e Codex chegam na mesma forma. O widget só filtra e formata.
    var claudeQuotas: [QuotaData] { quotas(for: "claude") }
    var codexQuotas: [QuotaData] { quotas(for: "codex") }

    private func quotas(for provider: String) -> [QuotaData] {
        guard let quotas = state?.quotas else { return [] }
        return quotas.filter { $0.provider == provider }.map { q in
            QuotaData(
                label: q.label,
                percent: q.utilization,
                status: q.status,
                resetText: Formatting.resetText(from: q.resetsAt),
                // frescor por item: o snapshot do Codex tem ritmo próprio
                isStale: Formatting.isStale(q.snapshotTs ?? state?.snapshotTs ?? state?.generatedAt),
                projected: q.projected,
                projectedText: q.projected.map { "proj. \(Formatting.fmtPercent($0)) até lá" }
            )
        }
    }

    // MARK: - Barra de menu

    /// Uma linha por quota disponível, com um rótulo curto pra caber na barra:
    /// janela geral do Claude vira "5h"/"7d"; janela de produto usa o nome
    /// ("Fable", "Codex") e só acrescenta a janela quando o mesmo produto tem
    /// mais de uma (Spark 5h / Spark 7d).
    struct MenuBarQuota: Identifiable {
        let id: String        // chave estável do backend
        let fullLabel: String // "Claude · Fable · 7 dias"
        let shortLabel: String
        let percent: Double?
        let status: String
    }

    var menuBarQuotas: [MenuBarQuota] {
        guard let quotas = state?.quotas else { return [] }
        var productCount: [String: Int] = [:]
        for q in quotas where q.product != nil {
            productCount[q.product!, default: 0] += 1
        }
        return quotas.map { q in
            let hours = q.windowHours.map { $0 >= 168 ? "7d" : "\(Int($0))h" } ?? ""
            let short: String
            if let product = q.product {
                let name = product.split(separator: "-").last.map(String.init) ?? product
                short = (productCount[product] ?? 0) > 1 ? "\(name) \(hours)" : name
            } else {
                short = hours
            }
            let prefix = q.provider == "claude" ? "Claude · " : "Codex · "
            return MenuBarQuota(id: q.key, fullLabel: prefix + q.label,
                                shortLabel: short, percent: q.utilization, status: q.status)
        }
    }

    /// Texto que vai ao lado do ícone na barra ("5h 15% · 7d 20%").
    func menuBarText(keys: Set<String>, showLabels: Bool) -> String? {
        let parts = menuBarQuotas.filter { keys.contains($0.id) }.map { q -> String in
            let value = q.percent.map { "\(Int($0.rounded()))%" } ?? "—"
            return showLabels && !q.shortLabel.isEmpty ? "\(q.shortLabel) \(value)" : value
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// Pior status entre as quotas mostradas na barra -- tinge o ícone.
    func worstStatus(keys: Set<String>) -> String {
        let order = ["SEGURO": 0, "INDETERMINADO": 0, "ATENCAO": 1, "RISCO": 2]
        return menuBarQuotas.filter { keys.contains($0.id) }
            .max { (order[$0.status] ?? 0) < (order[$1.status] ?? 0) }?.status ?? "INDETERMINADO"
    }

    var switchMessage: String? { state?.switch?.message }
    var switchStatus: String? { state?.switch?.verdict }

    var claudeBurnText: String? {
        guard let burn = state?.burnTokph, !burn.isEmpty else { return nil }
        return Formatting.fmtTokensPerHour(burn.values.reduce(0, +))
    }

    var codexBurnText: String? {
        guard let value = state?.chatgpt?.burnTokph else { return nil }
        return Formatting.fmtTokensPerHour(value)
    }

    var footerMessage: String {
        if isOffline {
            return "Painel offline -- confira se o backend está rodando em localhost:8090."
        }
        if let err = state?.lastError, !err.isEmpty {
            return err
        }
        if state?.authConnected == false {
            return "Conta desconectada -- abra o painel completo pra reconectar."
        }
        if state?.extraUsage?.burning == true {
            return "Claude usando créditos extras"
        }
        return "Claude + Codex · assinaturas separadas"
    }

    var footerIsAlert: Bool {
        isOffline
            || state?.authConnected == false
            || (state?.lastError?.isEmpty == false)
            || state?.extraUsage?.burning == true
    }
}
