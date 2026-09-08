import Foundation

enum AIUsageAPI {
    static let baseURL = URL(string: "http://localhost:8090")!

    static func fetchState() async throws -> AppState {
        let (data, _) = try await URLSession.shared.data(from: baseURL.appendingPathComponent("api/state"))
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(AppState.self, from: data)
    }
}
