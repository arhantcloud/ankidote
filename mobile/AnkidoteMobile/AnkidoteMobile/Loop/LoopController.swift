import Foundation
import SwiftUI

extension AnkidoteState {
    /// Fold a finished topic result into the persisted blob (port of
    /// `loop.apply_result`): update the topic's score, recompute the overall
    /// range, and accumulate practice tallies.
    mutating func applyResult(_ result: TopicScore) {
        var diag = raw["diagnostic"] as? [String: Any] ?? [:]
        var topicScores = diag["topicScores"] as? [[String: Any]] ?? []
        if let idx = topicScores.firstIndex(where: { ($0["topic"] as? String) == result.topic }) {
            topicScores[idx] = result.asDict
        } else {
            topicScores.append(result.asDict)
        }
        diag["topicScores"] = topicScores

        let estimates: [(theta: Double, se: Double, weight: Double)] = topicScores.compactMap { e in
            guard let answered = JSONNum.int(e["questionsAnswered"]), answered > 0,
                  let topic = e["topic"] as? String else { return nil }
            return (JSONNum.double(e["theta"]) ?? 0,
                    JSONNum.double(e["standardError"]) ?? 1.0,
                    ItemBank.shared.topicWeight(topic))
        }
        if !estimates.isEmpty {
            let combined = Scores.combineTopics(estimates)
            let rng = Scores.scoreRange(theta: combined.theta, se: combined.se)
            diag["baseline"] = Int((Double(rng.low) + Double(rng.high)) / 2.0)
            diag["low"] = rng.low
            diag["high"] = rng.high
        }
        raw["diagnostic"] = diag

        var progress = raw["progress"] as? [String: Any] ?? [:]
        progress["problemsAnswered"] = (JSONNum.int(progress["problemsAnswered"]) ?? 0) + result.questionsAnswered
        progress["problemsCorrect"] = (JSONNum.int(progress["problemsCorrect"]) ?? 0) + result.questionsCorrect
        var sessions = progress["sessions"] as? [[String: Any]] ?? []
        sessions.append([
            "ts": Int(Date().timeIntervalSince1970 * 1000),
            "topic": result.topic,
            "count": result.questionsAnswered,
            "correct": result.questionsCorrect,
        ])
        if sessions.count > 30 { sessions = Array(sessions.suffix(30)) }
        progress["sessions"] = sessions
        raw["progress"] = progress

        diagnostic = Diagnostic(diag)
    }
}

/// The visible stage of the study loop.
enum LoopPhase: Equatable {
    case cards(topic: String)
    case problems(topic: String)
    case topicSummary
    case done
}

struct AnswerFeedback {
    let chosen: Int
    let correct: Int
    let explanation: String
    var wasCorrect: Bool { chosen == correct }
}

/// Drives one weak-topic-at-a-time loop: study cards → answer 2–3 adaptive
/// practice problems → re-estimate the topic and pick the next weakest.
@MainActor
final class LoopController: ObservableObject {
    @Published var phase: LoopPhase = .done
    @Published var currentTopic: TopicInfo?
    @Published var cards: [Flashcard] = []
    @Published var currentProblem: Problem?
    @Published var feedback: AnswerFeedback?
    @Published var lastResult: TopicScore?
    @Published var topicAnswered = 0
    @Published var state: AnkidoteState

    let maxProblems = 3

    private let collection: CollectionStore?
    private var session: CatSession?
    private var skipped: Set<String> = []

    init(state: AnkidoteState, collection: CollectionStore?) {
        self.state = state
        self.collection = collection
    }

    var target: Int { state.targetScore }
    var overall: ScoreRange? {
        guard let d = state.diagnostic else { return nil }
        return ScoreRange(low: d.low, high: d.high)
    }

    func start() { selectNextTopic() }

    private func selectNextTopic() {
        feedback = nil
        currentProblem = nil
        session = nil
        guard let topic = TopicSelection.selectTopic(diagnostic: state.diagnostic, target: target, exclude: skipped) else {
            phase = .done
            currentTopic = nil
            return
        }
        currentTopic = topic
        cards = collection?.flashcards(topic: topic.topic) ?? []
        phase = .cards(topic: topic.topic)
    }

    func beginProblems() {
        guard let topic = currentTopic else { return }
        let theta0 = state.diagnostic?.theta(for: topic.topic) ?? 0
        session = CatSession(topic: topic.topic, section: topic.section, theta0: theta0)
        topicAnswered = 0
        phase = .problems(topic: topic.topic)
        advanceProblem()
    }

    private func advanceProblem() {
        feedback = nil
        guard let session else { return }
        if let item = session.nextItem() {
            currentProblem = item
        } else {
            finishTopic()
        }
    }

    func answer(_ choice: Int) {
        guard let session, let item = currentProblem, feedback == nil else { return }
        let correct = item.isCorrect(choice)
        session.record(item, correct: correct)
        topicAnswered = session.answered
        feedback = AnswerFeedback(chosen: choice, correct: item.correct, explanation: item.explanation)
    }

    func continueAfterAnswer() { advanceProblem() }

    private func finishTopic() {
        guard let session else { return }
        let result = session.result()
        lastResult = result
        state.applyResult(result)
        currentProblem = nil
        phase = .topicSummary
    }

    func skipCurrentTopic() {
        if let t = currentTopic { skipped.insert(t.topic) }
        selectNextTopic()
    }

    func nextTopic() { selectNextTopic() }
}
