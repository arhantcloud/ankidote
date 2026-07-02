// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

use std::collections::HashMap;

use anki_proto::ankidote::DiagnosticQuestion;
use anki_proto::ankidote::DiagnosticState;
use anki_proto::ankidote::GetPracticeQuestionsRequest;
use anki_proto::ankidote::NextDiagnosticQuestionResponse;
use anki_proto::ankidote::PracticeQuestion;
use anki_proto::ankidote::PracticeQuestions;
use anki_proto::ankidote::TopicScore;

use super::diagnostic_finished;
use super::estimate_theta;
use super::information;
use super::score_range;
use super::standard_error;
use super::BankQuestion;
use super::QUESTION_BANK;
use crate::collection::Collection;
use crate::error::Result;

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

        // grade the responses so far, ignoring unknown ids
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
            response.topic_scores = topic_scores(&responses);
        } else {
            // adaptive selection: unanswered item with maximum information
            // at the current ability estimate
            response.question = QUESTION_BANK
                .iter()
                .filter(|q| !answered_ids.contains(&q.id))
                .max_by(|x, y| information(theta, x).total_cmp(&information(theta, y)))
                .map(Into::into);
        }

        Ok(response)
    }
}

fn topic_scores(responses: &[(&BankQuestion, bool)]) -> Vec<TopicScore> {
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
