import SwiftUI

struct AccountCardView: View {
    let title: String
    let color: Color
    let quotas: [QuotaData]
    let burnText: String?
    var subtitle: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Circle().fill(color).frame(width: 8, height: 8)
                Text(title)
                    .font(.system(size: 13, weight: .semibold))
                    .lineLimit(1)
            }
            if let subtitle {
                Text(subtitle)
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            if quotas.isEmpty {
                Text("Sem dados ainda")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            } else {
                ForEach(quotas) { quota in
                    QuotaRowView(quota: quota, accountColor: color)
                }
            }

            Divider().overlay(AppColors.cardBorder)

            HStack {
                Text("Ritmo desta máquina")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
                Spacer()
                Text(burnText ?? "—")
                    .font(.system(size: 11, weight: .medium))
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 14)
                .fill(AppColors.cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(AppColors.cardBorder, lineWidth: 1)
        )
    }
}
