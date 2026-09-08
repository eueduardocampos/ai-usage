import Foundation

/// Espelha o JSON de GET /api/state (server.py:build_state). Só declara os
/// campos que o widget usa -- o resto do payload é ignorado pelo Decodable.
struct AppState: Decodable {
    let generatedAt: String
    let snapshotTs: String?
    let authConnected: Bool
    let rateLimited: Bool
    let lastError: String?
    let quotas: [Quota]?
    let burnTokph: [String: Double]?
    let extraUsage: ExtraUsage?
    let chatgpt: ChatGPTState?
    let `switch`: SwitchVerdict?
    let update: UpdateInfo?
}

/// Aviso de versão nova: o backend consulta as releases uma vez (cache de 6h)
/// e todas as superfícies leem daqui, em vez de cada uma bater no GitHub.
struct UpdateInfo: Decodable {
    let current: String
    let latest: String?
    let url: String
    let available: Bool
}

/// Um item de `quotas[]`: contrato único do backend para Claude e Codex,
/// já com rótulo, projeção e semáforo calculados (server.py:_quotas).
struct Quota: Decodable {
    let provider: String
    let key: String
    let label: String
    let product: String?
    let windowHours: Double?
    let utilization: Double?
    let resetsAt: String?
    let projected: Double?
    let status: String
    let snapshotTs: String?
}

struct ExtraUsage: Decodable {
    let burning: Bool
}

struct ChatGPTState: Decodable {
    let available: Bool
    let burnTokph: Double?
    let limitsError: String?
}

/// `switch` do /api/state: "posso trocar de modelo?" -- a informação mais
/// acionável do sistema, calculada por forecast.py.
struct SwitchVerdict: Decodable {
    let verdict: String
    let message: String
    let targetLabel: String?
}
