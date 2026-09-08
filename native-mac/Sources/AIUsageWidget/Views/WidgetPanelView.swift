import SwiftUI

/// Réplica nativa de web/src/Widget.jsx: header com indicador "ao vivo",
/// dois cartões (Claude/Codex) lado a lado, footer com alerta/mensagem.
struct WidgetPanelView: View {
    @EnvironmentObject var store: AppStateStore
    @EnvironmentObject var prefs: AppPreferences
    let onOpenDashboard: () -> Void
    let onClose: () -> Void
    let onMinimize: () -> Void
    let onZoom: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: prefs.layout == .compacto ? 8 : 12) {
            header

            switch prefs.layout {
            case .padrao:
                HStack(alignment: .top, spacing: 12) { cards }
                footer
            case .vertical:
                VStack(alignment: .leading, spacing: 12) { cards }
                Spacer(minLength: 0)
                footer
            case .compacto:
                compact
            }
        }
        .padding(prefs.layout == .compacto ? 12 : 16)
        .background(
            // Sem sombra desenhada aqui -- é a NSPanel (AppDelegate) que
            // recorta a view num arredondado de verdade e deixa o macOS
            // gerar a sombra nativa em cima desse formato.
            // style: .continuous pra bater com o cornerCurve da NSPanel
            // (AppDelegate) -- senão as duas curvas não coincidem exatamente
            // e sobra uma friesta bem fina na borda.
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(AppColors.cardBackground)
        )
        // Sem stroke manual por cima: junto com o recorte duro do layer da
        // NSPanel (AppDelegate), uma borda de 1pt desenhada aqui dobra a
        // aparência da linha e fica mais grossa/escura do que devia. Painéis
        // nativos (Notas, Lembretes) não têm contorno explícito -- só a
        // sombra do sistema já separa o card do fundo.
    }

    /// Semáforo desenhado dentro do próprio card (não é o das janelas do
    /// sistema) -- fica junto do nome do app, à esquerda; o status "ao
    /// vivo" e a hora da última atualização ficam à direita.
    /// Cotas zeradas somem quando o usuário pede (semana sem Codex, modelo
    /// que ele não usou). Nunca esconde tudo: se está tudo zerado, mostra
    /// tudo, senão o widget fica vazio sem explicação.
    private var claudeQuotas: [QuotaData] { visible(store.claudeQuotas) }
    private var codexQuotas: [QuotaData] { visible(store.codexQuotas) }

    private func visible(_ quotas: [QuotaData]) -> [QuotaData] {
        guard prefs.hideIdleQuotas else { return quotas }
        let all = store.claudeQuotas + store.codexQuotas
        guard all.contains(where: { !$0.isIdle }) else { return quotas }
        return quotas.filter { !$0.isIdle }
    }

    @ViewBuilder
    private var cards: some View {
        AccountCardView(title: "Claude", color: AppColors.claude,
                        quotas: claudeQuotas, burnText: store.claudeBurnText)
        AccountCardView(title: "Codex", color: AppColors.codex,
                        quotas: codexQuotas, burnText: store.codexBurnText)
    }

    /// Formato compacto: só as linhas de cota, agrupadas por conta, sem
    /// cartão, sem reset e sem rodapé.
    @ViewBuilder
    private var compact: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach([("Claude", AppColors.claude, claudeQuotas),
                     ("Codex", AppColors.codex, codexQuotas)], id: \.0) { name, color, quotas in
                if !quotas.isEmpty {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(name)
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(.secondary)
                        ForEach(quotas) { quota in
                            CompactRowView(quota: quota, accountColor: color)
                        }
                    }
                }
            }
            if claudeQuotas.isEmpty && codexQuotas.isEmpty {
                Text(store.footerMessage)
                    .font(.system(size: 10))
                    .foregroundStyle(store.footerIsAlert ? AppColors.alert : .secondary)
            }
        }
    }

    private var header: some View {
        HStack {
            HStack(spacing: 8) {
                TrafficLightButton(color: .red, symbol: "xmark", action: onClose)
                TrafficLightButton(color: .yellow, symbol: "minus", action: onMinimize)
                TrafficLightButton(color: .green, symbol: "arrow.up.left.and.arrow.down.right", action: onZoom)
            }

            if prefs.layout != .compacto {
                Text("AI Usage")
                    .font(.system(size: 13, weight: .bold))
                    .padding(.leading, 4)
            }

            Spacer()

            // No compacto o espaço é curto: só a bolinha de "ao vivo", sem o
            // texto e sem a hora da última leitura.
            if prefs.layout == .compacto {
                Circle()
                    .fill(store.isLive ? AppColors.codex : Color.gray)
                    .frame(width: 7, height: 7)
                    .help(store.lastUpdatedText)
            } else {
                VStack(alignment: .trailing, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(store.isLive ? "Ao vivo" : "Conectando")
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                        Circle()
                            .fill(store.isLive ? AppColors.codex : Color.gray)
                            .frame(width: 7, height: 7)
                    }
                    Text(store.lastUpdatedText)
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                }
            }

            Button(action: onOpenDashboard) {
                Image(systemName: "arrow.up.right.square")
            }
            .buttonStyle(.plain)
            .padding(.leading, 8)
        }
    }

    private var footer: some View {
        HStack {
            Text(store.footerMessage)
                .font(.system(size: 10))
                .foregroundStyle(store.footerIsAlert ? AppColors.alert : .secondary)
            Spacer()
            Button("Painel completo ↗", action: onOpenDashboard)
                .buttonStyle(.plain)
                .font(.system(size: 10, weight: .medium))
        }
    }
}

/// Botão redondo estilo "semáforo" do macOS, mas desenhado em SwiftUI e
/// vivendo dentro do card -- não é um botão nativo da janela. Mostra o
/// glifo (x / − / ⤢) só no hover, igual ao comportamento real do sistema.
private struct TrafficLightButton: View {
    let color: Color
    let symbol: String
    let action: () -> Void

    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle().fill(color)
                if hovering {
                    Image(systemName: symbol)
                        .font(.system(size: 6, weight: .bold))
                        .foregroundStyle(.black.opacity(0.55))
                }
            }
            .frame(width: 12, height: 12)
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}
