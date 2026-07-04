// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

use std::collections::HashMap;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;

use anki_proto::ankidote::AnkidoteStateJson;
use anki_proto::ankidote::AnkidoteStats;
use anki_proto::ankidote::DiagnosticProgress;
use anki_proto::ankidote::DiagnosticQuestion;
use anki_proto::ankidote::DiagnosticState;
use anki_proto::ankidote::GetAnkidoteStateRequest;
use anki_proto::ankidote::GetAnkidoteStatsRequest;
use anki_proto::ankidote::GetLoopStateRequest;
use anki_proto::ankidote::GetPracticeQuestionsRequest;
use anki_proto::ankidote::LoopProblem;
use anki_proto::ankidote::LoopProblemProgress;
use anki_proto::ankidote::LoopProblemStep;
use anki_proto::ankidote::LoopState;
use anki_proto::ankidote::NextDiagnosticQuestionResponse;
use anki_proto::ankidote::PracticeHistoryEntry;
use anki_proto::ankidote::PracticeQuestion;
use anki_proto::ankidote::PracticeQuestions;
use anki_proto::ankidote::SetAnkidoteStateRequest;
use anki_proto::ankidote::SortDecksRequest;
use anki_proto::ankidote::SortDecksResponse;
use anki_proto::ankidote::TopicMastery;
use anki_proto::ankidote::TopicScore;
use serde_json::json;
use serde_json::Value;

use super::diagnostic_finished;
use super::engine;
use super::engine::Params;
use super::engine::Stopper;
use super::estimate_theta;
use super::information;
use super::score_range;
use super::standard_error;
use super::BankQuestion;
use super::QUESTION_BANK;
use crate::collection::Collection;
use crate::deckconfig::DeckConfig;
use crate::deckconfig::ReviewCardOrder;
use crate::error::Result;
use crate::search::SortMode;

/// Name of the shared deck config that drives the points-at-stake queue.
const ANKIDOTE_DECK_CONFIG_NAME: &str = "Ankidote";
/// A card counts as mastered once its interval exceeds this many days.
const MASTERY_IVL: i64 = 3;

impl From<&BankQuestion> for PracticeQuestion {
    fn from(q: &BankQuestion) -> Self {
        PracticeQuestion {
            id: q.id,
            section: q.section_enum() as i32,
            topic: q.topic.clone(),
            subtopic: q.subtopic.clone(),
            stem: q.stem.clone(),
            choices: q.choices.clone(),
            correct_choice: q.correct,
            explanation: q.explanation.clone(),
            discrimination: q.a as f32,
            difficulty: q.b as f32,
            guessing: q.c as f32,
        }
    }
}

impl From<&BankQuestion> for DiagnosticQuestion {
    fn from(q: &BankQuestion) -> Self {
        DiagnosticQuestion {
            id: q.id,
            section: q.section_enum() as i32,
            topic: q.topic.clone(),
            subtopic: q.subtopic.clone(),
            stem: q.stem.clone(),
            choices: q.choices.clone(),
        }
    }
}

/// Live state of one topic's adaptive session, reconstructed statelessly from
/// the cumulative answers.
struct TopicSession {
    topic: String,
    theta: f64,
    se: f64,
    answered: usize,
    correct: usize,
    stopped: bool,
    administered: Vec<i64>,
}

impl crate::services::AnkidoteService for Collection {
    fn get_practice_questions(
        &mut self,
        input: GetPracticeQuestionsRequest,
    ) -> Result<PracticeQuestions> {
        let questions: Vec<PracticeQuestion> = QUESTION_BANK
            .iter()
            .filter(|q| input.topic.is_empty() || q.topic == input.topic)
            .take(if input.limit == 0 {
                usize::MAX
            } else {
                input.limit as usize
            })
            .map(Into::into)
            .collect();
        Ok(PracticeQuestions { questions })
    }

    fn next_diagnostic_question(
        &mut self,
        input: DiagnosticState,
    ) -> Result<NextDiagnosticQuestionResponse> {
        let by_id: HashMap<i64, &BankQuestion> = QUESTION_BANK.iter().map(|q| (q.id, q)).collect();

        let responses: Vec<(&BankQuestion, bool)> = input
            .answers
            .iter()
            .filter_map(|a| {
                by_id
                    .get(&a.question_id)
                    .map(|q| (*q, a.chosen_choice == q.correct))
            })
            .collect();
        let answered_ids: Vec<i64> = responses.iter().map(|(q, _)| q.id).collect();
        let answered_questions: Vec<&BankQuestion> = responses.iter().map(|(q, _)| *q).collect();

        let theta = estimate_theta(&responses);
        let se = standard_error(theta, &answered_questions);
        let finished = diagnostic_finished(responses.len(), se);

        let mut response = NextDiagnosticQuestionResponse {
            finished,
            question: None,
            answered: responses.len() as u32,
            max_questions: QUESTION_BANK.len() as u32,
            theta: theta as f32,
            standard_error: se as f32,
            score: Some(score_range(theta, se)),
            topic_scores: vec![],
        };

        if finished {
            response.topic_scores = legacy_topic_scores(&responses);
        } else {
            response.question = QUESTION_BANK
                .iter()
                .filter(|q| !answered_ids.contains(&q.id))
                .max_by(|x, y| information(theta, x).total_cmp(&information(theta, y)))
                .map(Into::into);
        }

        Ok(response)
    }

    /// Multi-topic adaptive diagnostic (the runner the apps drive). Ported
    /// from `pylib/anki/ankidote/runner.py`.
    fn run_diagnostic(
        &mut self,
        input: DiagnosticProgress,
    ) -> Result<NextDiagnosticQuestionResponse> {
        let by_id: HashMap<i64, &BankQuestion> = QUESTION_BANK.iter().map(|q| (q.id, q)).collect();

        // Grade every response so far, preserving order.
        let graded: Vec<(&BankQuestion, bool)> = input
            .answers
            .iter()
            .filter_map(|a| {
                by_id
                    .get(&a.question_id)
                    .map(|q| (*q, a.chosen_choice == q.correct))
            })
            .collect();
        let total_answered = graded.len();

        // Build one session per topic.
        let mut sessions: Vec<TopicSession> = Vec::new();
        for info in engine::topic_tree() {
            let prior = engine::seed_theta(
                input
                    .confidence
                    .get(&info.topic)
                    .or_else(|| input.confidence.get(&info.section))
                    .copied(),
            );
            let pool_len = QUESTION_BANK
                .iter()
                .filter(|q| q.topic == info.topic)
                .count();
            let topic_responses: Vec<(&BankQuestion, bool)> = graded
                .iter()
                .filter(|(q, _)| q.topic == info.topic)
                .copied()
                .collect();
            let params: Vec<Params> = topic_responses.iter().map(|(q, _)| q.params()).collect();
            let bools: Vec<bool> = topic_responses.iter().map(|(_, c)| *c).collect();

            let theta = if params.is_empty() {
                prior
            } else {
                engine::estimate_theta(&params, &bools, prior)
            };
            let se = if params.is_empty() {
                engine::MAX_SE
            } else {
                engine::standard_error(theta, &params)
            };
            let answered = topic_responses.len();
            let remaining = pool_len.saturating_sub(answered);
            let stopper = Stopper {
                target_se: 0.45,
                min_items: 1,
                max_items: 3,
            };
            sessions.push(TopicSession {
                topic: info.topic.clone(),
                theta,
                se,
                answered,
                correct: bools.iter().filter(|c| **c).count(),
                stopped: stopper.should_stop(answered, se, remaining),
                administered: topic_responses.iter().map(|(q, _)| q.id).collect(),
            });
        }

        let max_questions = if input.max_questions > 0 {
            input.max_questions
        } else {
            (QUESTION_BANK.len() as u32).min(16)
        };
        let finished = total_answered as u32 >= max_questions || sessions.iter().all(|s| s.stopped);

        // Overall score, weighting each answered topic by its GMAT contribution.
        let estimates: Vec<(f64, f64, f64)> = sessions
            .iter()
            .filter(|s| s.answered > 0)
            .map(|s| (s.theta, s.se, engine::topic_weight(&s.topic)))
            .collect();
        let (overall_theta, overall_se) = engine::combine_topics(&estimates);

        let topic_scores: Vec<TopicScore> = sessions
            .iter()
            .filter(|s| s.answered > 0)
            .map(|s| TopicScore {
                topic: s.topic.clone(),
                theta: s.theta as f32,
                standard_error: s.se as f32,
                score: Some(engine::score_range(s.theta, s.se)),
                questions_answered: s.answered as u32,
                questions_correct: s.correct as u32,
            })
            .collect();

        let mut response = NextDiagnosticQuestionResponse {
            finished,
            question: None,
            answered: total_answered as u32,
            max_questions,
            theta: overall_theta as f32,
            standard_error: overall_se as f32,
            score: Some(engine::score_range(overall_theta, overall_se)),
            topic_scores,
        };

        if !finished {
            // Advance the least-certain open session; break ties toward the
            // least-sampled topic so early questions spread across topics.
            let mut open: Vec<&TopicSession> = sessions.iter().filter(|s| !s.stopped).collect();
            open.sort_by(|a, b| {
                b.se.total_cmp(&a.se)
                    .then_with(|| a.answered.cmp(&b.answered))
            });
            for session in open {
                let next = QUESTION_BANK
                    .iter()
                    .filter(|q| q.topic == session.topic && !session.administered.contains(&q.id))
                    .max_by(|x, y| {
                        information(session.theta, x).total_cmp(&information(session.theta, y))
                    });
                if let Some(q) = next {
                    response.question = Some(q.into());
                    break;
                }
            }
        }

        Ok(response)
    }

    /// Stateless per-topic practice-problem phase (ported from `loop.py`).
    fn run_loop_problems(&mut self, input: LoopProblemProgress) -> Result<LoopProblemStep> {
        let pool = engine::problem_pool(&input.topic);

        let mut params: Vec<Params> = Vec::new();
        let mut bools: Vec<bool> = Vec::new();
        let mut answered_indices: Vec<usize> = Vec::new();
        for ans in &input.answers {
            if let Some(problem) = pool.get(ans.index as usize) {
                params.push(problem.params);
                bools.push(ans.chosen_choice as usize == problem.correct);
                answered_indices.push(ans.index as usize);
            }
        }

        let theta = if params.is_empty() {
            input.theta0
        } else {
            engine::estimate_theta(&params, &bools, input.theta0)
        };
        let se = if params.is_empty() {
            engine::MAX_SE
        } else {
            engine::standard_error(theta, &params)
        };
        let answered = params.len();
        let remaining = pool.len().saturating_sub(answered);
        let stopper = Stopper {
            target_se: 0.5,
            min_items: 2,
            max_items: 3,
        };
        let finished = stopper.should_stop(answered, se, remaining);

        let mut step = LoopProblemStep {
            finished,
            problem: None,
            answered: answered as u32,
            correct: bools.iter().filter(|c| **c).count() as u32,
            theta,
            standard_error: se,
            score: Some(engine::score_range(theta, se)),
        };

        if !finished {
            step.problem = pool
                .iter()
                .filter(|p| !answered_indices.contains(&p.index))
                .max_by(|x, y| {
                    engine::information(theta, x.params)
                        .total_cmp(&engine::information(theta, y.params))
                })
                .map(|p| LoopProblem {
                    index: p.index as u32,
                    topic: p.topic.clone(),
                    subtopic: p.subtopic.clone(),
                    stem: p.stem.clone(),
                    choices: p.choices.clone(),
                });
        }

        Ok(step)
    }

    fn apply_loop_result(&mut self, input: TopicScore) -> Result<AnkidoteStateJson> {
        let mut blob = self.ankidote_blob();
        apply_loop_result_to_blob(&mut blob, &input);
        self.write_ankidote_blob(&blob)?;
        Ok(AnkidoteStateJson {
            json: blob.to_string(),
        })
    }

    fn get_loop_state(&mut self, _input: GetLoopStateRequest) -> Result<LoopState> {
        let blob = self.ankidote_blob();
        let target = engine::target_from_blob(&blob);
        let measured = engine::measured_from_blob(blob.get("diagnostic"));

        let Some(info) = engine::select_topic(&measured, target, &[]) else {
            return Ok(LoopState {
                phase: "empty".into(),
                ..Default::default()
            });
        };

        let name = engine::deck_name(&info.topic);
        let total = self.count_cards(&format!("deck:\"{name}\""))?;
        let immature = self.count_cards(&format!("deck:\"{name}\" prop:ivl<{MASTERY_IVL}"))?;
        let to_study = self.count_cards(&format!("deck:\"{name}\" (is:due or is:new)"))?;
        let mastered = total.saturating_sub(immature);

        let topic_score = blob
            .get("diagnostic")
            .and_then(|d| d.get("topicScores"))
            .and_then(Value::as_array)
            .and_then(|arr| {
                arr.iter()
                    .find(|e| e.get("topic").and_then(Value::as_str) == Some(&info.topic))
                    .and_then(|e| e.get("score").cloned())
            });

        let payload = json!({
            "total": total,
            "immature": immature,
            "mastered": mastered,
            "toStudy": to_study,
            "topicScore": topic_score,
        });
        let phase = if total == 0 || to_study > 0 {
            "cards"
        } else {
            "day_done"
        };

        Ok(LoopState {
            phase: phase.into(),
            topic: info.topic.clone(),
            section: info.section.clone(),
            section_label: engine::section_label(&info.section).into(),
            deck: name,
            weight: info.weight,
            payload_json: payload.to_string(),
        })
    }

    fn get_ankidote_state(&mut self, _input: GetAnkidoteStateRequest) -> Result<AnkidoteStateJson> {
        Ok(AnkidoteStateJson {
            json: self.ankidote_blob().to_string(),
        })
    }

    fn set_ankidote_state(&mut self, input: SetAnkidoteStateRequest) -> Result<AnkidoteStateJson> {
        let mut blob = self.ankidote_blob();
        if let Ok(Value::Object(partial)) = serde_json::from_str::<Value>(&input.partial_json) {
            let obj = blob.as_object_mut().expect("ankidote blob is an object");
            for (k, v) in partial {
                obj.insert(k, v);
            }
        }
        self.write_ankidote_blob(&blob)?;
        Ok(AnkidoteStateJson {
            json: blob.to_string(),
        })
    }

    fn get_ankidote_stats(&mut self, _input: GetAnkidoteStatsRequest) -> Result<AnkidoteStats> {
        let blob = self.ankidote_blob();
        let progress = blob.get("progress").cloned().unwrap_or_else(|| json!({}));

        let mut topic_mastery = Vec::new();
        let mut total_mastered = 0u32;
        let mut total_in_topics = 0u32;
        for info in engine::topic_tree() {
            let name = engine::deck_name(&info.topic);
            let total = self.count_cards(&format!("deck:\"{name}\""))?;
            let immature = self.count_cards(&format!("deck:\"{name}\" prop:ivl<{MASTERY_IVL}"))?;
            let mastered = total.saturating_sub(immature);
            total_mastered += mastered;
            total_in_topics += total;
            if total > 0 {
                topic_mastery.push(TopicMastery {
                    topic: info.topic.clone(),
                    section: info.section.clone(),
                    mastered,
                    total,
                    pct: ((mastered as f64 / total as f64) * 100.0).round() as u32,
                });
            }
        }
        let mastery_pct = if total_in_topics > 0 {
            ((total_mastered as f64 / total_in_topics as f64) * 100.0).round() as i32
        } else {
            -1
        };

        let graded_reviews: u32 = self
            .storage
            .db
            .query_row(
                "select count() from revlog where type in (0,1,2)",
                [],
                |r| r.get(0),
            )
            .unwrap_or(0);

        let now_ms = now_millis();
        let sessions = progress
            .get("sessions")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut history = Vec::new();
        for session in sessions.iter().rev().take(8) {
            let count = session.get("count").and_then(Value::as_i64).unwrap_or(0);
            let correct = session.get("correct").and_then(Value::as_i64).unwrap_or(0);
            let ts = session.get("ts").and_then(Value::as_i64).unwrap_or(now_ms);
            let days = ((now_ms - ts) / 86_400_000).max(0);
            history.push(PracticeHistoryEntry {
                days_ago: days as u32,
                topic: session
                    .get("topic")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                count: count as u32,
                accuracy: if count > 0 {
                    ((correct as f64 / count as f64) * 100.0).round() as u32
                } else {
                    0
                },
            });
        }

        Ok(AnkidoteStats {
            problems_answered: progress
                .get("problemsAnswered")
                .and_then(Value::as_i64)
                .unwrap_or(0) as u32,
            problems_correct: progress
                .get("problemsCorrect")
                .and_then(Value::as_i64)
                .unwrap_or(0) as u32,
            sessions: history,
            graded_reviews,
            topic_mastery,
            mastery_pct,
            mastered_cards: total_mastered,
            total_cards: total_in_topics,
        })
    }

    fn sort_decks(&mut self, _input: SortDecksRequest) -> Result<SortDecksResponse> {
        // Ensure a dedicated deck config that uses the points-at-stake queue,
        // and assign every Ankidote topic deck to it, so the study loop
        // actually exercises the scheduler change.
        let config_id = self.ensure_points_at_stake_config()?;
        let mut total = 0u32;
        for info in engine::topic_tree() {
            let name = engine::deck_name(&info.topic);
            let mut deck = self.get_or_create_normal_deck(&name)?;
            if deck.config_id() != Some(config_id) {
                deck.normal_mut()?.config_id = config_id.0;
                self.add_or_update_deck(&mut deck)?;
            }
            total += self.count_cards(&format!("deck:\"{name}\""))?;
        }
        Ok(SortDecksResponse { total })
    }
}

// -- Collection helpers -----------------------------------------------------

impl Collection {
    fn ankidote_blob(&self) -> Value {
        super::read_blob(self)
    }

    fn write_ankidote_blob(&mut self, blob: &Value) -> Result<()> {
        self.set_config_json(super::ANKIDOTE_CONFIG_KEY, blob, false)?;
        Ok(())
    }

    fn count_cards(&mut self, search: &str) -> Result<u32> {
        Ok(self.search_cards(search, SortMode::NoOrder)?.len() as u32)
    }

    /// Find (or create) the shared deck config whose review order is the
    /// points-at-stake queue.
    fn ensure_points_at_stake_config(&mut self) -> Result<crate::deckconfig::DeckConfigId> {
        let existing = self
            .storage
            .get_deck_config_map()?
            .into_values()
            .find(|c| c.name == ANKIDOTE_DECK_CONFIG_NAME);
        let mut config = match existing {
            Some(config) => config,
            None => DeckConfig {
                name: ANKIDOTE_DECK_CONFIG_NAME.to_string(),
                ..Default::default()
            },
        };
        let target = ReviewCardOrder::PointsAtStake as i32;
        if config.id.0 == 0 || config.inner.review_order != target {
            config.inner.review_order = target;
            self.add_or_update_deck_config(&mut config)?;
        }
        Ok(config.id)
    }
}

// -- pure helpers -----------------------------------------------------------

fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

/// Fold a finished topic result into the persisted blob (ported from
/// `loop.apply_result`).
fn apply_loop_result_to_blob(blob: &mut Value, result: &TopicScore) {
    let obj = blob.as_object_mut().expect("ankidote blob is an object");

    let diagnostic = obj
        .entry("diagnostic")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .expect("diagnostic is an object");
    let topic_scores = diagnostic
        .entry("topicScores")
        .or_insert_with(|| json!([]))
        .as_array_mut()
        .expect("topicScores is an array");

    let range = result.score.unwrap_or_default();
    let entry = json!({
        "topic": result.topic,
        "section": engine::section_for_topic(&result.topic),
        "theta": result.theta,
        "standardError": result.standard_error,
        "score": { "low": range.low, "high": range.high },
        "questionsAnswered": result.questions_answered,
        "questionsCorrect": result.questions_correct,
    });
    if let Some(existing) = topic_scores
        .iter_mut()
        .find(|e| e.get("topic").and_then(Value::as_str) == Some(&result.topic))
    {
        *existing = entry;
    } else {
        topic_scores.push(entry);
    }

    // Recompute the overall range from all measured topics.
    let estimates: Vec<(f64, f64, f64)> = topic_scores
        .iter()
        .filter(|e| {
            e.get("questionsAnswered")
                .and_then(Value::as_i64)
                .unwrap_or(0)
                > 0
        })
        .map(|e| {
            (
                e.get("theta").and_then(Value::as_f64).unwrap_or(0.0),
                e.get("standardError")
                    .and_then(Value::as_f64)
                    .unwrap_or(1.0),
                engine::topic_weight(e.get("topic").and_then(Value::as_str).unwrap_or("")),
            )
        })
        .collect();
    if !estimates.is_empty() {
        let (theta, se) = engine::combine_topics(&estimates);
        let range = engine::score_range(theta, se);
        diagnostic.insert("baseline".into(), json!(((range.low + range.high) / 2)));
        diagnostic.insert("low".into(), json!(range.low));
        diagnostic.insert("high".into(), json!(range.high));
    }

    // Accumulate real practice tallies for the dashboard.
    let progress = obj
        .entry("progress")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .expect("progress is an object");
    let answered = result.questions_answered as i64;
    let correct = result.questions_correct as i64;
    let prev_answered = progress
        .get("problemsAnswered")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let prev_correct = progress
        .get("problemsCorrect")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    progress.insert("problemsAnswered".into(), json!(prev_answered + answered));
    progress.insert("problemsCorrect".into(), json!(prev_correct + correct));
    let sessions = progress
        .entry("sessions")
        .or_insert_with(|| json!([]))
        .as_array_mut()
        .expect("sessions is an array");
    sessions.push(json!({
        "ts": now_millis(),
        "topic": result.topic,
        "count": answered,
        "correct": correct,
    }));
    // Keep the log bounded (it lives in the synced config blob).
    if sessions.len() > 30 {
        let drop = sessions.len() - 30;
        sessions.drain(0..drop);
    }
}

/// Legacy single-pool per-topic scoring for `NextDiagnosticQuestion`.
fn legacy_topic_scores(responses: &[(&BankQuestion, bool)]) -> Vec<TopicScore> {
    let mut by_topic: HashMap<&str, Vec<(&BankQuestion, bool)>> = HashMap::new();
    for (q, correct) in responses {
        by_topic
            .entry(q.topic.as_str())
            .or_default()
            .push((*q, *correct));
    }
    let mut scores: Vec<TopicScore> = by_topic
        .into_iter()
        .map(|(topic, responses)| {
            let questions: Vec<&BankQuestion> = responses.iter().map(|(q, _)| *q).collect();
            let theta = estimate_theta(&responses);
            let se = standard_error(theta, &questions);
            TopicScore {
                topic: topic.into(),
                theta: theta as f32,
                standard_error: se as f32,
                score: Some(score_range(theta, se)),
                questions_answered: responses.len() as u32,
                questions_correct: responses.iter().filter(|(_, c)| *c).count() as u32,
            }
        })
        .collect();
    scores.sort_by(|a, b| a.theta.total_cmp(&b.theta));
    scores
}
