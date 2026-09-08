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
}
