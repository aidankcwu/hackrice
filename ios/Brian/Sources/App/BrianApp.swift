// @main. One AppState for the whole app, handed to every screen through the environment.
import SwiftUI

@main
struct BrianApp: App {
    @State private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appState)
        }
    }
}
