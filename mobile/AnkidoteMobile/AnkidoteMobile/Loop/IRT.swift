import Foundation

/// Item Response Theory primitives — a direct port of `pylib/anki/ankidote/irt.py`
/// and `scores.py` so the mobile loop re-estimates ability and score bands
/// identically to the desktop.
enum IRT {
    /// Logistic scaling constant (normal-ogive scale), matching catsim's default.
    static let D = 1.702
    static let thetaMin = -4.0
    static let thetaMax = 4.0
    static let maxSE = 1.5

    struct Params {
        let a: Double
        let b: Double
        let c: Double
    }

    /// 3PL probability of a correct response at the given ability.
    static func pCorrect(theta: Double, _ p: Params) -> Double {
        p.c + (1.0 - p.c) / (1.0 + exp(-D * p.a * (theta - p.b)))
    }

    /// Fisher information contributed by an item at `theta` (3PL).
    static func information(theta: Double, _ p: Params) -> Double {
        let prob = pCorrect(theta: theta, p)
        if prob <= p.c || prob >= 1.0 { return 0.0 }
        let quotient = (prob - p.c) / (1.0 - p.c)
        return pow(D * p.a, 2) * (quotient * quotient) * (1.0 - prob) / prob
    }

    private static func logLikelihood(theta: Double, params: [Params], responses: [Bool]) -> Double {
        var total = 0.0
        for (p, correct) in zip(params, responses) {
            let prob = min(max(pCorrect(theta: theta, p), 1e-9), 1.0 - 1e-9)
            total += correct ? log(prob) : log(1.0 - prob)
        }
        return total
    }

    /// Maximum-likelihood ability estimate via coarse-then-fine grid search.
    static func estimateTheta(params: [Params], responses: [Bool], prior: Double = 0.0) -> Double {
        if responses.isEmpty { return prior }

        if Set(responses).count == 1 {
            // Monotonic likelihood → pin toward the evidence, nudged by prior.
            let direction = responses[0] ? 1.0 : -1.0
            return max(thetaMin, min(thetaMax, prior + direction * 1.5))
        }

        var bestTheta = prior
        var bestLL = -Double.infinity
        // (scale, span): coarse full-range pass, then a fine refine around best.
        let passes: [(scale: Double, span: Double?)] = [(0.05, nil), (0.005, 0.1)]
        for pass in passes {
            let lo = pass.span == nil ? thetaMin : max(thetaMin, bestTheta - pass.span!)
            let hi = pass.span == nil ? thetaMax : min(thetaMax, bestTheta + pass.span!)
            let steps = Int(((hi - lo) / pass.scale).rounded())
            for i in 0 ... max(steps, 0) {
                let theta = lo + Double(i) * pass.scale
                let ll = logLikelihood(theta: theta, params: params, responses: responses)
                if ll > bestLL {
                    bestLL = ll
                    bestTheta = theta
                }
            }
        }
        return bestTheta
    }

    /// Standard error of the ability estimate = 1/sqrt(test information).
    static func standardError(theta: Double, params: [Params]) -> Double {
        let totalInfo = params.reduce(0.0) { $0 + information(theta: theta, $1) }
        if totalInfo <= 0.0 { return maxSE }
        return min(1.0 / sqrt(totalInfo), maxSE)
    }
}

/// theta/SE → GMAT score-range mapping (port of `scores.py`).
enum Scores {
    static let scoreMid = 505.0
    static let scorePerTheta = 100.0
    static let scoreLow = 205
    static let scoreHigh = 805
    static let scoreStep = 10
    static let z = 1.96

    static func thetaToScore(_ theta: Double) -> Int {
        let raw = scoreMid + theta * scorePerTheta
        let clamped = min(max(raw, Double(scoreLow)), Double(scoreHigh))
        let steps = Int(((clamped - Double(scoreLow)) / Double(scoreStep)).rounded())
        let maxSteps = (scoreHigh - scoreLow) / scoreStep
        return scoreLow + min(steps, maxSteps) * scoreStep
    }

    static func scoreRange(theta: Double, se: Double) -> ScoreRange {
        ScoreRange(low: thetaToScore(theta - z * se), high: thetaToScore(theta + z * se))
    }

    /// Combine per-topic (theta, se, weight) into overall (theta, se).
    static func combineTopics(_ estimates: [(theta: Double, se: Double, weight: Double)]) -> (theta: Double, se: Double) {
        let weighted = estimates.filter { $0.weight > 0 }
        if weighted.isEmpty { return (0.0, 1.5) }
        let totalW = weighted.reduce(0.0) { $0 + $1.weight }
        let theta = weighted.reduce(0.0) { $0 + $1.theta * $1.weight } / totalW
        let variance = weighted.reduce(0.0) { $0 + pow($1.weight / totalW, 2) * $1.se * $1.se }
        return (theta, sqrt(variance))
    }
}
