import Foundation

enum Formatting {
    private static let isoParser: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let isoParserNoFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func parseISO8601(_ raw: String?) -> Date? {
        guard let raw else { return nil }
        return isoParser.date(from: raw) ?? isoParserNoFraction.date(from: raw)
    }

    /// "6.3M/h", "820k/h", "42/h" -- mesma escala usada em web/src/format.js.
    static func fmtTokensPerHour(_ value: Double) -> String {
        let v = abs(value)
        if v >= 1_000_000 {
            return String(format: "%.1fM/h", value / 1_000_000)
        }
        if v >= 1_000 {
            return String(format: "%.1fk/h", value / 1_000)
        }
        return String(format: "%.0f/h", value)
    }

    static func fmtPercent(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%.0f%%", value)
    }

    /// Porta de resetAt() em web/src/Widget.jsx: "Reset em Xh Ym" / "Reset em
    /// Xd Yh" / "Aguardando reset" / "Reset indisponível".
    static func resetText(from resetsAt: String?, now: Date = Date()) -> String {
        guard let resetsAt, let date = parseISO8601(resetsAt) else {
            return "Reset indisponível"
        }
        let diff = date.timeIntervalSince(now)
        if diff <= 0 {
            return "Aguardando reset"
        }
        if diff < 86_400 {
            let hours = Int(diff / 3600)
            let minutes = Int((diff.truncatingRemainder(dividingBy: 3600)) / 60)
            return "Reset em \(hours)h \(minutes)m"
        }
        let days = Int(diff / 86_400)
        let hours = Int((diff.truncatingRemainder(dividingBy: 86_400)) / 3600)
        return "Reset em \(days)d \(hours)h"
    }

    /// Dado "ao vivo" se tem menos de 30s; "desatualizado" acima de 15min --
    /// mesmos limiares do widget web.
    static func isLive(_ generatedAt: String?, now: Date = Date()) -> Bool {
        guard let generatedAt, let date = parseISO8601(generatedAt) else { return false }
        return now.timeIntervalSince(date) < 30
    }

    static func isStale(_ generatedAt: String?, now: Date = Date()) -> Bool {
        guard let generatedAt, let date = parseISO8601(generatedAt) else { return true }
        return now.timeIntervalSince(date) > 15 * 60
    }
}
