import SwiftUI

/// Escolhe o que aparece como texto na barra de menu (como o AlDente/Stats
/// fazem com bateria e temperatura). A lista vem do backend: quando uma janela
/// nova aparece -- o teto semanal do Fable, por exemplo -- ela surge aqui
/// sozinha, sem precisar de nova versão do app.
struct PreferencesView: View {
    @EnvironmentObject var store: AppStateStore
    @EnvironmentObject var prefs: AppPreferences
    @State private var loginError: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Formato do widget").font(.system(size: 13, weight: .semibold))
                Picker("", selection: $prefs.layout) {
                    ForEach(WidgetLayout.allCases) { layout in
                        Text(layout.title).tag(layout)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                Text(prefs.layout.detail)
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                Toggle("Ocultar cotas zeradas", isOn: $prefs.hideIdleQuotas)
                    .font(.system(size: 12))
                    .padding(.top, 2)
            }

            Divider()

            VStack(alignment: .leading, spacing: 6) {
                Text("Mostrar na barra de menu").font(.system(size: 13, weight: .semibold))
                Text(previewText)
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(.secondary)
            }

            let quotas = store.menuBarQuotas
            if quotas.isEmpty {
                Text("Nenhuma cota disponível ainda. Se o painel estiver fora do ar, abra o painel completo pelo menu.")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(quotas) { quota in
                        Toggle(isOn: binding(for: quota.id)) {
                            HStack(spacing: 6) {
                                Text(quota.fullLabel)
                                Text(quota.percent.map { "\(Int($0.rounded()))%" } ?? "—")
                                    .foregroundStyle(.secondary)
                            }
                            .font(.system(size: 12))
                        }
                    }
                }
                Toggle("Mostrar rótulo antes do número", isOn: $prefs.showLabels)
                    .font(.system(size: 12))
            }

            Divider()

            Toggle("Abrir ao iniciar sessão", isOn: Binding(
                get: { prefs.opensAtLogin },
                set: { newValue in
                    do { try prefs.setOpensAtLogin(newValue); loginError = nil }
                    catch { loginError = error.localizedDescription }
                }
            ))
            .font(.system(size: 12))
            .disabled(!prefs.canManageLoginItem)

            if !prefs.canManageLoginItem {
                Text("Disponível só no app instalado (rodando por `swift run` não há bundle para registrar).")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
            if let loginError {
                Text(loginError).font(.system(size: 10)).foregroundStyle(AppColors.statusRisco)
            }
        }
        .padding(20)
        .frame(width: 380, alignment: .leading)
    }

    private var previewText: String {
        store.menuBarText(keys: prefs.menuBarQuotaKeys, showLabels: prefs.showLabels)
            ?? "só o ícone"
    }

    private func binding(for key: String) -> Binding<Bool> {
        Binding(
            get: { prefs.menuBarQuotaKeys.contains(key) },
            set: { on in
                if on { prefs.menuBarQuotaKeys.insert(key) } else { prefs.menuBarQuotaKeys.remove(key) }
            }
        )
    }
}
