import Foundation

// MARK: - Loose JSON helpers

/// The synced `ankidote` blob is authored by the desktop app in Python, where
/// numbers can be ints or floats and a few fields are optional. We parse from a
/// loose `[String: Any]` (JSONSerialization) rather than strict Codable so we
/// tolerate that variance and any extra fields.
enum JSONNum {
    static func int(_ v: Any?) -> Int? {
        switch v {
        case let n as Int: return n
        case let n as Double: return Int(n.rounded())
        case let n as NSNumber: return n.intValue
        case let s as String: return Int(s) ?? Double(s).map { Int($0.rounded()) }
        default: return nil
        }
    }

    static func double(_ v: Any?) -> Double? {
        switch v {
        case let n as Double: return n
        case let n as Int: return Double(n)
        case let n as NSNumber: return n.doubleValue
        case let s as String: return Double(s)
        default: return nil
        }
    }

    static func string(_ v: Any?) -> String? {
        if let s = v as? String { return s }
        if let n = v as? NSNumber { return n.stringValue }
        return nil
    }
}

// MARK: - Sections

enum Section: String, CaseIterable {
    case quant
    case verbal
    case dataInsights = "data_insights"

    var label: String {
        switch self {
        case .quant: return "Quant"
        case .verbal: return "Verbal"
        case .dataInsights: return "Data Insights"
        }
    }

    /// Section weight toward the GMAT total (even thirds for v1, matching
    /// `topics.py`'s `SECTION_WEIGHT`).
    var weight: Double { 1.0 / 3.0 }

    /// Accepts the section as a slug string or a numeric id (1/2/3), matching
    /// `item_bank.py`'s `SECTION_IDS`.
    static func from(_ any: Any?) -> Section? {
        if let s = any as? String { return Section(rawValue: s) }
        switch JSONNum.int(any) {
        case 1: return .quant
        case 2: return .verbal
        case 3: return .dataInsights
        default: return nil
        }
    }
}

// MARK: - Persisted state models

struct ScoreRange: Equatable {
    var low: Int
    var high: Int
    var mid: Int { Int((Double(low) + Double(high)) / 2.0) }
}

struct TopicScore {
    var topic: String
    var section: Section
    var theta: Double
    var standardError: Double
    var score: ScoreRange
    var questionsAnswered: Int
    var questionsCorrect: Int

    init(topic: String, section: Section, theta: Double, standardError: Double,
         score: ScoreRange, questionsAnswered: Int, questionsCorrect: Int) {
        self.topic = topic
        self.section = section
        self.theta = theta
        self.standardError = standardError
        self.score = score
        self.questionsAnswered = questionsAnswered
        self.questionsCorrect = questionsCorrect
    }

    init?(_ dict: [String: Any]) {
        guard let topic = JSONNum.string(dict["topic"]) else { return nil }
        self.topic = topic
        self.section = Section.from(dict["section"]) ?? ItemBank.shared.section(for: topic) ?? .quant
        self.theta = JSONNum.double(dict["theta"]) ?? 0
        self.standardError = JSONNum.double(dict["standardError"]) ?? 1.0
        let rng = dict["score"] as? [String: Any] ?? [:]
        self.score = ScoreRange(low: JSONNum.int(rng["low"]) ?? 405,
                                high: JSONNum.int(rng["high"]) ?? 405)
        self.questionsAnswered = JSONNum.int(dict["questionsAnswered"]) ?? 0
        self.questionsCorrect = JSONNum.int(dict["questionsCorrect"]) ?? 0
    }

    var asDict: [String: Any] {
        [
            "topic": topic,
            "section": section.rawValue,
            "theta": theta,
            "standardError": standardError,
            "score": ["low": score.low, "high": score.high],
            "questionsAnswered": questionsAnswered,
            "questionsCorrect": questionsCorrect,
        ]
    }
}

struct Diagnostic {
    var baseline: Int
    var low: Int
    var high: Int
    var answered: Int
    var topicScores: [TopicScore]

    init?(_ dict: [String: Any]) {
        self.baseline = JSONNum.int(dict["baseline"]) ?? 0
        self.low = JSONNum.int(dict["low"]) ?? 0
        self.high = JSONNum.int(dict["high"]) ?? 0
        self.answered = JSONNum.int(dict["answered"]) ?? 0
        let raw = dict["topicScores"] as? [Any] ?? []
        self.topicScores = raw.compactMap { ($0 as? [String: Any]).flatMap(TopicScore.init) }
    }

    func score(for topic: String) -> Int? {
        topicScores.first { $0.topic == topic }?.score.mid
    }

    func theta(for topic: String) -> Double? {
        topicScores.first { $0.topic == topic }?.theta
    }
}

struct Plan {
    var desiredScore: Int
    var testDate: String?
    var baseline: Int?

    init?(_ dict: [String: Any]) {
        guard let target = JSONNum.int(dict["desiredScore"]) else { return nil }
        self.desiredScore = target
        self.testDate = JSONNum.string(dict["testDate"])
        self.baseline = JSONNum.int(dict["baseline"])
    }
}

/// The full parsed state plus the raw dict, so loop updates can be folded back
/// into the original structure.
struct AnkidoteState {
    var raw: [String: Any]
    var diagnostic: Diagnostic?
    var plan: Plan?
    var loggedIn: Bool

    init(raw: [String: Any]) {
        self.raw = raw
        self.diagnostic = (raw["diagnostic"] as? [String: Any]).flatMap(Diagnostic.init)
        self.plan = (raw["plan"] as? [String: Any]).flatMap(Plan.init)
        self.loggedIn = (raw["loggedIn"] as? Bool) ?? false
    }

    var targetScore: Int { plan?.desiredScore ?? 655 }

    var hasDiagnostic: Bool {
        (diagnostic?.topicScores.isEmpty == false)
    }
}
