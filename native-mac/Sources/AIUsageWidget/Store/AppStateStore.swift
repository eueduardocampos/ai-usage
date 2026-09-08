import Combine
import Foundation

struct QuotaData: Identifiable {
    let id = UUID()
    let label: String
    let percent: Double?
    let status: String
    let resetText: String
    let isStale: Bool
    /// Projeção pro reset ("onde deve chegar", não só o valor atual). nil
    /// quando a API não traz isso pronto (é o caso do Codex hoje).
    let projectedText: String?
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
                projectedText: q.projected.map { "proj. \(Formatting.fmtPercent($0)) até lá" }
            )
        }
    }

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
