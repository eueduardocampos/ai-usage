import SwiftUI

/// Uma cota em uma linha só, para o formato compacto. Mostra o valor atual E
/// a projeção até o reset -- o valor atual sozinho qualquer app mostra; a
/// projeção é o que este painel tem de diferente, então ela fica mesmo no
/// formato menor, em texto ("15% → 23%") e na barra (trecho mais claro).
struct CompactRowView: View {
    let quota: QuotaData
    let accountColor: Color

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

    private var showsProjection: Bool {
        guard let projected = quota.projected, !quota.isStale else { return false }
        return projected > (quota.percent ?? 0) + 0.5
    }

    var body: some View {
        HStack(spacing: 7) {
            Circle().fill(accountColor).frame(width: 6, height: 6)
            Text(quota.label)
                .font(.system(size: 11))
                .lineLimit(1)
                .truncationMode(.tail)

            Spacer(minLength: 6)

            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.08))
                if showsProjection, let projected = quota.projected {
                    Capsule().fill(barColor.opacity(0.3))
                        .frame(width: 44 * fraction(projected))
                }
                Capsule().fill(barColor).frame(width: 44 * fraction(quota.percent))
            }
            .frame(width: 44, height: 4)

            HStack(spacing: 2) {
                Text(Formatting.fmtPercent(quota.percent))
                    .font(.system(size: 11, weight: .semibold))
                if showsProjection {
                    Text("→")
                        .font(.system(size: 9))
                        .foregroundStyle(.tertiary)
                    Text(Formatting.fmtPercent(quota.projected))
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                }
            }
            .monospacedDigit()
            .frame(width: 78, alignment: .trailing)
            .help(showsProjection
                  ? "Agora \(Formatting.fmtPercent(quota.percent)) · projeção de \(Formatting.fmtPercent(quota.projected)) no reset"
                  : quota.resetText)
        }
    }
}
