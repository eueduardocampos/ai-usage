import SwiftUI

struct QuotaRowView: View {
    let quota: QuotaData
    let accountColor: Color

    /// A cor segue o semáforo do backend (calculado sobre a PROJEÇÃO até o
    /// reset, não sobre o valor atual): é o que o forecast existe pra dizer.
    private var barColor: Color {
        if quota.isStale { return AppColors.barStale }
        switch quota.status {
        case "RISCO": return AppColors.barDanger
        case "ATENCAO": return AppColors.statusAtencao
        default: return accountColor
        }
    }

    private func fraction(_ value: Double?) -> Double {
        min(max((value ?? 0) / 100, 0), 1)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(quota.label)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
                Spacer()
                Text(Formatting.fmtPercent(quota.percent))
                    .font(.system(size: 12, weight: .semibold))
            }

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    // .primary inverte sozinho com o tema: sutil em claro e
                    // em escuro (branco fixo sumia no fundo claro).
                    Capsule().fill(Color.primary.opacity(0.08))
                    // Trecho fantasma: até onde a cota deve chegar no reset.
                    if let projected = quota.projected, projected > (quota.percent ?? 0), !quota.isStale {
                        Capsule()
                            .fill(barColor.opacity(0.3))
                            .frame(width: geo.size.width * fraction(projected))
                    }
                    Capsule()
                        .fill(barColor)
                        .frame(width: geo.size.width * fraction(quota.percent))
                }
            }
            .frame(height: 6)

            HStack(spacing: 4) {
                Text(quota.isStale ? "Dado desatualizado" : quota.resetText)
                if !quota.isStale, let projectedText = quota.projectedText {
                    Text("·")
                    Text(projectedText)
                }
            }
            .font(.system(size: 10))
            .foregroundStyle(quota.isStale ? AppColors.barStale : .secondary)
        }
    }
}
