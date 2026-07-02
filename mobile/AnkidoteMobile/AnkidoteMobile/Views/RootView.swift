import SwiftUI

struct RootView: View {
    @StateObject private var app = AppState()

    var body: some View {
        Group {
            switch app.phase {
            case .login:
                LoginView(app: app)
            case .syncing(let message):
                SyncView(message: message)
            case .loop:
                if let loop = app.loop {
                    LoopView(app: app, loop: loop)
                } else {
                    SyncView(message: "Preparing…")
                }
            case .error(let message):
                ErrorView(message: message) { app.reset() }
            }
        }
        .onAppear {
            // UI-test / demo hook: `simctl launch <id> <bundle> --demo`.
            let args = ProcessInfo.processInfo.arguments
            if args.contains("--demo"), case .login = app.phase {
                app.startDemo()
                if args.contains("--problems") { app.loop?.beginProblems() }
            }
        }
    }
}

struct SyncView: View {
    let message: String
    var body: some View {
        VStack(spacing: 20) {
            ProgressView().controlSize(.large).tint(.white)
            Text(message)
                .font(.callout)
                .foregroundStyle(.white.opacity(0.85))
                .multilineTextAlignment(.center)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .screenBackground()
    }
}

struct ErrorView: View {
    let message: String
    let onDismiss: () -> Void
    var body: some View {
        VStack(spacing: 22) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 44))
                .foregroundStyle(Theme.bad)
            Text("Something went wrong")
                .font(.title3.bold())
                .foregroundStyle(.white)
            Text(message)
                .font(.callout)
                .foregroundStyle(.white.opacity(0.8))
                .multilineTextAlignment(.center)
            Button("Back", action: onDismiss)
                .buttonStyle(PrimaryButtonStyle())
        }
        .padding(30)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .screenBackground()
    }
}
