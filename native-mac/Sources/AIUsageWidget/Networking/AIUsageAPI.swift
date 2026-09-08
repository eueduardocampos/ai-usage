import Foundation

enum AIUsageAPI {
    static let baseURL = URL(string: "http://localhost:8090")!

    static func fetchState() async throws -> AppState {
        let (data, _) = try await URLSession.shared.data(from: baseURL.appendingPathComponent("api/state"))
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(AppState.self, from: data)
    }

    /// POST /api/refresh -- o backend recusa se for cedo demais ou se a conta
    /// estiver em rate limit, e devolve o motivo + quantos segundos faltam.
    struct RefreshResult: Decodable {
        let ok: Bool
        let reason: String?
        let retryIn: Int?
    }

    @discardableResult
    static func refresh() async throws -> RefreshResult {
        var req = URLRequest(url: baseURL.appendingPathComponent("api/refresh"))
        req.httpMethod = "POST"
        let (data, _) = try await URLSession.shared.data(for: req)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(RefreshResult.self, from: data)
    }

    /// GET /auth/start -- o backend abre o navegador e captura o callback;
    /// o app nativo não tem OAuth próprio.
    static func startAuth() async throws {
        _ = try await URLSession.shared.data(from: baseURL.appendingPathComponent("auth/start"))
    }
}
