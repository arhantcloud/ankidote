import SwiftUI

enum Theme {
    static let accent = Color(red: 0.36, green: 0.42, blue: 0.95)
    static let accentSoft = Color(red: 0.36, green: 0.42, blue: 0.95).opacity(0.14)
    static let good = Color(red: 0.20, green: 0.70, blue: 0.45)
    static let bad = Color(red: 0.86, green: 0.30, blue: 0.34)

    static var background: LinearGradient {
        LinearGradient(
            colors: [Color(red: 0.07, green: 0.09, blue: 0.16),
                     Color(red: 0.12, green: 0.13, blue: 0.22)],
            startPoint: .top, endPoint: .bottom
        )
    }
}

struct Card<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        content
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(.white.opacity(0.08)))
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    var enabled = true
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 15)
            .background(enabled ? Theme.accent : Color.gray.opacity(0.4),
                        in: RoundedRectangle(cornerRadius: 14))
            .opacity(configuration.isPressed ? 0.8 : 1)
    }
}

extension View {
    func screenBackground() -> some View {
        background(Theme.background.ignoresSafeArea())
            .preferredColorScheme(.dark)
    }
}
