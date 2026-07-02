// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! GMAT practice/diagnostic support for Ankidote.
//!
//! Questions are served from a built-in bank (items.json) calibrated with
//! 3PL IRT parameters. The diagnostic is adaptive: ability (theta) is
//! re-estimated after every response, and the next question is the
//! unanswered item with maximum Fisher information at the current estimate.

mod service;

use anki_proto::ankidote::Section;
use once_cell::sync::Lazy;
use serde::Deserialize;

#[derive(Deserialize)]
pub(crate) struct BankQuestion {
    pub id: i64,
    pub section: String,
    pub topic: String,
    pub subtopic: String,
    pub stem: String,
    pub choices: Vec<String>,
    pub correct: u32,
    pub explanation: String,
    /// 3PL parameters: discrimination, difficulty, guessing.
    pub a: f64,
    pub b: f64,
    pub c: f64,
}

impl BankQuestion {
    pub(crate) fn section_enum(&self) -> Section {
        match self.section.as_str() {
            "quant" => Section::Quant,
            "verbal" => Section::Verbal,
            "data_insights" => Section::DataInsights,
            _ => Section::Unspecified,
        }
    }
}

pub(crate) static QUESTION_BANK: Lazy<Vec<BankQuestion>> =
    Lazy::new(|| serde_json::from_str(include_str!("items.json")).expect("valid items.json"));

/// Scaling constant for the logistic IRT models.
const D: f64 = 1.7;
/// Stop once at least this many questions are answered and the error is low.
const MIN_QUESTIONS: usize = 8;
const TARGET_SE: f64 = 0.45;
/// Fallback SE reported before any information has been collected.
const MAX_SE: f64 = 1.5;
/// theta -> GMAT total score mapping: score = SCORE_MID + theta * SCORE_PER_THETA.
const SCORE_MID: f64 = 505.0;
const SCORE_PER_THETA: f64 = 100.0;

pub(crate) fn probability_correct(theta: f64, q: &BankQuestion) -> f64 {
    q.c + (1.0 - q.c) / (1.0 + (-D * q.a * (theta - q.b)).exp())
}

/// Fisher information of an item at the given ability level (3PL).
pub(crate) fn information(theta: f64, q: &BankQuestion) -> f64 {
    let p = probability_correct(theta, q);
    let quot = (p - q.c) / (1.0 - q.c);
    (D * q.a).powi(2) * quot.powi(2) * (1.0 - p) / p
}

/// Maximum-likelihood ability estimate via grid search over [-3, 3].
pub(crate) fn estimate_theta(responses: &[(&BankQuestion, bool)]) -> f64 {
    if responses.is_empty() {
        return 0.0;
    }
    let mut best_theta = 0.0;
    let mut best_ll = f64::NEG_INFINITY;
    let mut theta = -3.0;
    while theta <= 3.0 {
        let ll: f64 = responses
            .iter()
            .map(|(q, correct)| {
                let p = probability_correct(theta, q).clamp(1e-6, 1.0 - 1e-6);
                if *correct {
                    p.ln()
                } else {
                    (1.0 - p).ln()
                }
            })
            .sum();
        if ll > best_ll {
            best_ll = ll;
            best_theta = theta;
        }
        theta += 0.01;
    }
    best_theta
}

pub(crate) fn standard_error(theta: f64, questions: &[&BankQuestion]) -> f64 {
    let total_info: f64 = questions.iter().map(|q| information(theta, q)).sum();
    if total_info <= 0.0 {
        MAX_SE
    } else {
        (1.0 / total_info.sqrt()).min(MAX_SE)
    }
}

/// Map an ability estimate to the GMAT total-score scale (205-805, scores
/// end in 5).
pub(crate) fn theta_to_score(theta: f64) -> u32 {
    let raw = SCORE_MID + theta * SCORE_PER_THETA;
    let clamped = raw.clamp(205.0, 805.0);
    let steps = ((clamped - 205.0) / 10.0).round() as u32;
    205 + steps.min(60) * 10
}

pub(crate) fn score_range(theta: f64, se: f64) -> anki_proto::ankidote::ScoreRange {
    anki_proto::ankidote::ScoreRange {
        low: theta_to_score(theta - 1.96 * se),
        high: theta_to_score(theta + 1.96 * se),
    }
}

pub(crate) fn diagnostic_finished(answered: usize, se: f64) -> bool {
    answered >= QUESTION_BANK.len() || (answered >= MIN_QUESTIONS && se <= TARGET_SE)
}
