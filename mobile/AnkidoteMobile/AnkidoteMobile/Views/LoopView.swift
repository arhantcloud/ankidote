import SwiftUI

struct LoopView: View {
    @ObservedObject var app: AppState
    @ObservedObject var loop: LoopController

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(.white.opacity(0.1))
            content
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .screenBackground()
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Study loop")
                    .font(.headline).foregroundStyle(.white)
                if let r = loop.overall {
                    Text("Now \(r.low)–\(r.high) · Target \(loop.target)")
                        .font(.caption).foregroundStyle(.white.opacity(0.65))
                }
            }
            Spacer()
            Button {
                app.reset()
            } label: {
                Image(systemName: "rectangle.portrait.and.arrow.right")
                    .foregroundStyle(.white.opacity(0.8))
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }

    @ViewBuilder
    private var content: some View {
        switch loop.phase {
        case .cards:
            CardsPhaseView(loop: loop)
        case .problems:
            ProblemPhaseView(loop: loop)
        case .topicSummary:
            TopicSummaryView(loop: loop)
        case .done:
            DoneView(loop: loop, app: app)
        }
    }
}

// MARK: - Topic banner

private struct TopicBanner: View {
    let topic: TopicInfo?
    var body: some View {
        if let topic {
            HStack(spacing: 8) {
                Text(topic.section.label.uppercased())
                    .font(.caption2.weight(.bold))
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(Theme.accentSoft, in: Capsule())
                    .foregroundStyle(Theme.accent)
                Text(topic.topic)
                    .font(.title3.bold())
                    .foregroundStyle(.white)
                Spacer()
            }
        }
    }
}

// MARK: - Cards phase

private struct CardsPhaseView: View {
    @ObservedObject var loop: LoopController
    @State private var index = 0
    @State private var showBack = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            TopicBanner(topic: loop.currentTopic)

            if loop.cards.isEmpty {
                Card {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("No synced cards for this topic")
                            .font(.headline).foregroundStyle(.white)
                        Text("You can still practice problems for it. Cards you create and sync on your computer will appear here.")
                            .font(.callout).foregroundStyle(.white.opacity(0.7))
                    }
                }
            } else {
                let card = loop.cards[min(index, loop.cards.count - 1)]
                Text("Card \(index + 1) of \(loop.cards.count) · tap to flip")
                    .font(.caption).foregroundStyle(.white.opacity(0.6))
                Button {
                    withAnimation(.easeInOut(duration: 0.15)) { showBack.toggle() }
                } label: {
                    Card {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(showBack ? "ANSWER" : "PROMPT")
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(Theme.accent)
                            Text(showBack ? card.back : card.front)
                                .font(.title3)
                                .foregroundStyle(.white)
                                .frame(maxWidth: .infinity, minHeight: 140, alignment: .topLeading)
                        }
                    }
                }
                .buttonStyle(.plain)

                HStack {
                    Button {
                        showBack = false
                        index = max(0, index - 1)
                    } label: { Label("Prev", systemImage: "chevron.left") }
                        .disabled(index == 0)
                    Spacer()
                    Button {
                        showBack = false
                        index = min(loop.cards.count - 1, index + 1)
                    } label: { Label("Next", systemImage: "chevron.right") }
                        .disabled(index >= loop.cards.count - 1)
                }
                .font(.subheadline.weight(.semibold))
                .tint(.white)
            }

            Spacer()

            Button("Start practice problems") { loop.beginProblems() }
                .buttonStyle(PrimaryButtonStyle())
            Button("Skip this topic") { loop.skipCurrentTopic() }
                .font(.subheadline).foregroundStyle(.white.opacity(0.6))
                .frame(maxWidth: .infinity)
        }
        .padding(20)
    }
}

// MARK: - Problems phase

private struct ProblemPhaseView: View {
    @ObservedObject var loop: LoopController

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            TopicBanner(topic: loop.currentTopic)
            ProgressView(value: Double(loop.topicAnswered), total: Double(loop.maxProblems))
                .tint(Theme.accent)

            if let problem = loop.currentProblem {
                Card {
                    Text(problem.stem)
                        .font(.title3.weight(.medium))
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                VStack(spacing: 10) {
                    ForEach(Array(problem.choices.enumerated()), id: \.offset) { idx, choice in
                        choiceRow(problem: problem, idx: idx, choice: choice)
                    }
                }

                if let fb = loop.feedback {
                    VStack(alignment: .leading, spacing: 8) {
                        Label(fb.wasCorrect ? "Correct" : "Not quite",
                              systemImage: fb.wasCorrect ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundStyle(fb.wasCorrect ? Theme.good : Theme.bad)
                            .font(.headline)
                        Text(fb.explanation)
                            .font(.callout).foregroundStyle(.white.opacity(0.8))
                    }
                    Button("Continue") { loop.continueAfterAnswer() }
                        .buttonStyle(PrimaryButtonStyle())
                }
            }
            Spacer()
        }
        .padding(20)
    }

    @ViewBuilder
    private func choiceRow(problem: Problem, idx: Int, choice: String) -> some View {
        let fb = loop.feedback
        let isCorrect = idx == problem.correct
        let isChosen = fb?.chosen == idx
        let bg: Color = {
            guard let fb else { return .white.opacity(0.07) }
            if isCorrect { return Theme.good.opacity(0.3) }
            if isChosen && !fb.wasCorrect { return Theme.bad.opacity(0.3) }
            return .white.opacity(0.05)
        }()
        Button {
            loop.answer(idx)
        } label: {
            HStack {
                Text(choice).foregroundStyle(.white)
                Spacer()
                if fb != nil, isCorrect {
                    Image(systemName: "checkmark").foregroundStyle(Theme.good)
                } else if let fb, isChosen, !fb.wasCorrect {
                    Image(systemName: "xmark").foregroundStyle(Theme.bad)
                }
            }
            .padding(14)
            .background(bg, in: RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
        .disabled(fb != nil)
    }
}

// MARK: - Topic summary

private struct TopicSummaryView: View {
    @ObservedObject var loop: LoopController

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            TopicBanner(topic: loop.currentTopic)
            if let r = loop.lastResult {
                Card {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("Topic updated")
                            .font(.headline).foregroundStyle(.white)
                        metric("Score range", "\(r.score.low)–\(r.score.high)")
                        metric("This round", "\(r.questionsCorrect)/\(r.questionsAnswered) correct")
                        metric("Ability (θ)", String(format: "%.2f", r.theta))
                    }
                }
                if let overall = loop.overall {
                    Text("Overall now \(overall.low)–\(overall.high), target \(loop.target).")
                        .font(.footnote).foregroundStyle(.white.opacity(0.65))
                }
            }
            Spacer()
            Button("Next topic") { loop.nextTopic() }
                .buttonStyle(PrimaryButtonStyle())
        }
        .padding(20)
    }

    private func metric(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(.white.opacity(0.6))
            Spacer()
            Text(value).foregroundStyle(.white).font(.headline)
        }
    }
}

// MARK: - Done

private struct DoneView: View {
    @ObservedObject var loop: LoopController
    let app: AppState

    var body: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 56)).foregroundStyle(Theme.good)
            Text("You're all caught up")
                .font(.title2.bold()).foregroundStyle(.white)
            if let r = loop.overall {
                Text("Estimated \(r.low)–\(r.high) · target \(loop.target)")
                    .font(.callout).foregroundStyle(.white.opacity(0.7))
            }
            Text("Every topic is at or above target, or has been covered this session. Come back after more study on your computer.")
                .font(.footnote).foregroundStyle(.white.opacity(0.6))
                .multilineTextAlignment(.center)
            Spacer()
            Button("Log out") { app.reset() }
                .buttonStyle(PrimaryButtonStyle())
        }
        .padding(24)
    }
}
