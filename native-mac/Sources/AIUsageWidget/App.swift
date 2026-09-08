import SwiftUI

@main
struct AIUsageWidgetApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        // Sem WindowGroup de propósito: toda a UI (menu bar + painel
        // flutuante) é montada manualmente pelo AppDelegate via AppKit.
        Settings {
            EmptyView()
        }
    }
}
