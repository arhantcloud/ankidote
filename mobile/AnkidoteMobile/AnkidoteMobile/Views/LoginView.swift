import SwiftUI

struct LoginView: View {
    @ObservedObject var app: AppState
    @State private var email = ""
    @State private var password = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Ankidote")
                        .font(.system(size: 40, weight: .bold, design: .rounded))
                        .foregroundStyle(.white)
                    Text("Sign in with AnkiWeb to continue your plan on the go.")
                        .font(.callout)
                        .foregroundStyle(.white.opacity(0.75))
                }
                .padding(.top, 30)

                Card {
                    VStack(alignment: .leading, spacing: 16) {
                        field(title: "AnkiWeb email", text: $email, secure: false,
                              keyboard: .emailAddress)
                        field(title: "Password", text: $password, secure: true, keyboard: .default)

                        Button {
                            Task { await app.loginAndSync(email: email.trimmingCharacters(in: .whitespaces),
                                                          password: password) }
                        } label: {
                            Text("Log in & sync")
                        }
                        .buttonStyle(PrimaryButtonStyle(enabled: canSubmit))
                        .disabled(!canSubmit)
                    }
                }

                HStack(spacing: 10) {
                    Image(systemName: "info.circle")
                    Text("Complete the diagnostic on your computer and sync to AnkiWeb first — this app picks up your plan and study loop from there.")
                }
                .font(.footnote)
                .foregroundStyle(.white.opacity(0.6))

                Button("Try the demo (offline)") { app.startDemo() }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.accent)
                    .frame(maxWidth: .infinity)
            }
            .padding(24)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .screenBackground()
    }

    private var canSubmit: Bool {
        !email.trimmingCharacters(in: .whitespaces).isEmpty && !password.isEmpty
    }

    @ViewBuilder
    private func field(title: String, text: Binding<String>, secure: Bool,
                       keyboard: UIKeyboardType) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.white.opacity(0.5))
            Group {
                if secure {
                    SecureField("", text: text)
                } else {
                    TextField("", text: text)
                        .keyboardType(keyboard)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
            }
            .foregroundStyle(.white)
            .padding(12)
            .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
        }
    }
}
