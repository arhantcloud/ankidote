import Foundation

struct Problem: Identifiable {
    let id: String
    let section: Section
    let topic: String
    let subtopic: String
    let stem: String
    let choices: [String]
    let correct: Int
    let explanation: String
    let params: IRT.Params

    func isCorrect(_ chosen: Int) -> Bool { chosen == correct }
}

/// Stable, seeded PRNG so a topic's problem pool is identical across launches
/// and devices. (Swift's `Hasher` is per-process randomized, so we seed from a
/// stable FNV-1a hash and stream with SplitMix64.)
struct SeededRNG {
    private var state: UInt64

    init(seed: String) {
        var hash: UInt64 = 0xcbf2_9ce4_8422_2325
        for byte in seed.utf8 {
            hash ^= UInt64(byte)
            hash = hash &* 0x0000_0100_0000_01B3
        }
        state = hash == 0 ? 0x9E37_79B9_7F4A_7C15 : hash
    }

    mutating func next() -> UInt64 {
        state = state &+ 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }

    /// Inclusive integer in [lo, hi].
    mutating func int(_ lo: Int, _ hi: Int) -> Int {
        guard hi > lo else { return lo }
        let span = UInt64(hi - lo + 1)
        return lo + Int(next() % span)
    }

    mutating func choice<T>(_ arr: [T]) -> T { arr[int(0, arr.count - 1)] }

    mutating func shuffle<T>(_ arr: inout [T]) {
        guard arr.count > 1 else { return }
        for i in stride(from: arr.count - 1, to: 0, by: -1) {
            let j = int(0, i)
            arr.swapAt(i, j)
        }
    }
}

/// Per-topic practice-problem store (port of `problems.py`). Problems are
/// deterministic, seeded arithmetic MCQs — a working stand-in until authored
/// problems are ingested behind the same interface.
enum Problems {
    static let poolSize = 12
    private static var pools: [String: [Problem]] = [:]

    private static func difficulty(_ index: Int) -> Double {
        if poolSize <= 1 { return 0.0 }
        return -1.5 + 3.0 * (Double(index) / Double(poolSize - 1))
    }

    private static func generate(topic: String) -> [Problem] {
        let section = ItemBank.shared.section(for: topic) ?? .quant
        var rng = SeededRNG(seed: "ankidote-problems::\(topic)")
        var pool: [Problem] = []
        for i in 0 ..< poolSize {
            let x = rng.int(6, 49)
            let y = rng.int(2, 19)
            let op = rng.choice(["+", "-", "×"])
            let answer: Int
            switch op {
            case "+": answer = x + y
            case "-": answer = x - y
            default: answer = x * y
            }
            var seen: Set<Int> = [answer]
            var choices = [answer]
            let deltas = [-9, -5, -3, -2, -1, 1, 2, 3, 5, 9]
            while choices.count < 5 {
                let cand = answer + rng.choice(deltas)
                if !seen.contains(cand) {
                    seen.insert(cand)
                    choices.append(cand)
                }
            }
            rng.shuffle(&choices)
            let correct = choices.firstIndex(of: answer) ?? 0
            pool.append(Problem(
                id: "prob::\(topic)::\(i)",
                section: section,
                topic: topic,
                subtopic: "Practice",
                stem: "[\(topic)] Sample practice problem #\(i + 1). Compute \(x) \(op) \(y).",
                choices: choices.map(String.init),
                correct: correct,
                explanation: "\(x) \(op) \(y) = \(answer).",
                params: IRT.Params(a: 1.0, b: difficulty(i), c: 0.2)
            ))
        }
        return pool
    }

    static func pool(topic: String) -> [Problem] {
        if let cached = pools[topic] { return cached }
        let generated = generate(topic: topic)
        pools[topic] = generated
        return generated
    }
}
