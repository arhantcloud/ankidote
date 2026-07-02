import Foundation

/// Stop rule for a topic's problem phase: 2–3 problems, or once the estimate is
/// precise enough (mirrors `loop.py`'s `Stopper(target_se: 0.5, min: 2, max: 3)`).
struct Stopper {
    var targetSE: Double = 0.5
    var minItems: Int = 2
    var maxItems: Int = 3

    func stopped(answered: Int, se: Double) -> Bool {
        if answered >= maxItems { return true }
        if answered >= minItems, se <= targetSE { return true }
        return false
    }
}

/// One topic's adaptive problem session: max-information item selection with
/// MLE θ re-estimation (port of `engine.CatSession` over a temp problem pool).
final class CatSession {
    let topic: String
    let section: Section
    private let pool: [Problem]
    private let prior: Double
    private let stopper: Stopper

    private(set) var theta: Double
    private(set) var se: Double
    private(set) var answered = 0
    private(set) var correct = 0
    private var usedParams: [IRT.Params] = []
    private var responses: [Bool] = []
    private var servedIDs: Set<String> = []

    init(topic: String, section: Section, theta0: Double = 0.0, stopper: Stopper = Stopper()) {
        self.topic = topic
        self.section = section
        self.pool = Problems.pool(topic: topic)
        self.prior = theta0
        self.theta = theta0
        self.stopper = stopper
        self.se = IRT.maxSE
    }

    var stopped: Bool { stopper.stopped(answered: answered, se: se) }

    /// The next, most-informative unserved problem at the current θ.
    func nextItem() -> Problem? {
        if stopped { return nil }
        var best: Problem?
        var bestInfo = -1.0
        for item in pool where !servedIDs.contains(item.id) {
            let info = IRT.information(theta: theta, item.params)
            if info > bestInfo {
                bestInfo = info
                best = item
            }
        }
        if best == nil {
            best = pool.first { !servedIDs.contains($0.id) }
        }
        if let best { servedIDs.insert(best.id) }
        return best
    }

    func record(_ item: Problem, correct isCorrect: Bool) {
        usedParams.append(item.params)
        responses.append(isCorrect)
        answered += 1
        if isCorrect { correct += 1 }
        theta = IRT.estimateTheta(params: usedParams, responses: responses, prior: prior)
        se = IRT.standardError(theta: theta, params: usedParams)
    }

    func result() -> TopicScore {
        let rng = Scores.scoreRange(theta: theta, se: se)
        return TopicScore(topic: topic, section: section, theta: theta,
                          standardError: se, score: rng,
                          questionsAnswered: answered, questionsCorrect: correct)
    }
}

/// Outer-loop topic selection (port of `topics.select_topic`): the topic whose
/// section-weighted gap to target is largest. Untested topics use a neutral
/// score so the loop still surfaces them; ties break toward the heavier topic.
enum TopicSelection {
    static let unknownScore = 405

    static func selectTopic(diagnostic: Diagnostic?, target: Int, exclude: Set<String> = []) -> TopicInfo? {
        var best: TopicInfo?
        var bestGap = -1.0
        for info in ItemBank.shared.topicTree() {
            if exclude.contains(info.topic) { continue }
            let score = diagnostic?.score(for: info.topic) ?? unknownScore
            let gap = info.weight * Double(max(0, target - score))
            if gap > bestGap || (gap == bestGap && best != nil && info.weight > best!.weight) {
                bestGap = gap
                best = info
            }
        }
        return best
    }
}
