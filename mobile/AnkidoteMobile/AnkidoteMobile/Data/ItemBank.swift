import Foundation

struct TopicInfo {
    let topic: String
    let section: Section
    let weight: Double
}

/// Loads the bundled `item_bank.json` (copied from the desktop app) to recover
/// the topic taxonomy, section membership, and per-topic weight — mirroring
/// `topics.py` / `item_bank.py` so deck names and topic selection line up with
/// what the desktop produced.
final class ItemBank {
    static let shared = ItemBank()

    private(set) var orderedTopics: [String] = []
    private var topicSection: [String: Section] = [:]

    private init() {
        load()
    }

    private func load() {
        guard let url = Bundle.main.url(forResource: "item_bank", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let root = try? JSONSerialization.jsonObject(with: data)
        else { return }
        let records: [[String: Any]]
        if let dict = root as? [String: Any], let items = dict["items"] as? [[String: Any]] {
            records = items
        } else if let arr = root as? [[String: Any]] {
            records = arr
        } else {
            records = []
        }
        var seen = Set<String>()
        for rec in records {
            guard let topic = rec["topic"] as? String else { continue }
            if !seen.contains(topic) {
                seen.insert(topic)
                orderedTopics.append(topic)
                topicSection[topic] = Section.from(rec["section"]) ?? .quant
            }
        }
    }

    func section(for topic: String) -> Section? { topicSection[topic] }

    /// One deck per topic, flat and colon-free (matches `topics.deck_name`).
    func deckName(for topic: String) -> String { "Ankidote \(topic)" }

    /// All topics with their share of the GMAT total (`topic_tree`).
    func topicTree() -> [TopicInfo] {
        var bySection: [Section: [String]] = [:]
        for topic in orderedTopics {
            let s = topicSection[topic] ?? .quant
            bySection[s, default: []].append(topic)
        }
        var out: [TopicInfo] = []
        for topic in orderedTopics {
            let s = topicSection[topic] ?? .quant
            let count = max(bySection[s]?.count ?? 1, 1)
            out.append(TopicInfo(topic: topic, section: s, weight: s.weight / Double(count)))
        }
        return out
    }

    func topicWeight(_ topic: String) -> Double {
        topicTree().first { $0.topic == topic }?.weight ?? 0
    }
}
