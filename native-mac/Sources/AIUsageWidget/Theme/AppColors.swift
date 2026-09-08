import SwiftUI

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: opacity
        )
    }

    /// Cor que troca de hex conforme a Aparência do Sistema (claro/escuro) --
    /// diferente do widget web (que força `color-scheme: dark`), o app nativo
    /// deve respeitar o tema do Mac.
    static func adaptive(light: UInt32, dark: UInt32) -> Color {
        Color(NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            let hex = isDark ? dark : light
            return NSColor(
                srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
                green: CGFloat((hex >> 8) & 0xFF) / 255,
                blue: CGFloat(hex & 0xFF) / 255,
                alpha: 1
            )
        })
    }
}

/// Paleta portada de web/src/Widget.jsx + web/src/widget.css + tray.py, com
/// fundo/borda do card adaptados pra claro/escuro (o original web é sempre
/// escuro; o app nativo segue o Sistema).
enum AppColors {
    static let cardBackground = Color.adaptive(light: 0xf5f6f8, dark: 0x101720)
    static let cardBorder = Color.adaptive(light: 0xdfe3e8, dark: 0x2a3541)

    static let claude = Color(hex: 0xef985d)
    static let codex = Color(hex: 0x45d6aa)

    static let statusSeguro = Color(hex: 0x34c759)
    static let statusAtencao = Color(hex: 0xff9f0a)
    static let statusRisco = Color(hex: 0xff453a)
    static let statusIndeterminado = Color(hex: 0x8e8e93)
    static let offline = Color(hex: 0x6c6c70)

    static let barDanger = Color(hex: 0xff7171)
    static let barStale = Color.adaptive(light: 0x9aa3ad, dark: 0x788493)
    static let alert = Color.adaptive(light: 0xb35900, dark: 0xffae73)

    static func forStatus(_ status: String) -> Color {
        switch status {
        case "SEGURO": return statusSeguro
        case "ATENCAO": return statusAtencao
        case "RISCO": return statusRisco
        default: return statusIndeterminado
        }
    }
}
