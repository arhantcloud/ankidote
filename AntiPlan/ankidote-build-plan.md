# Ankidote — Build Plan

Step-by-step build-out of `ankidote-prd.md`, reusing the existing Anki backend
wherever possible. Each PRD feature is planned here (reuse vs. new, where it
lives) before implementation. Phases are ordered so the **basic study loop**
works end-to-end before any of the "extra" loop features (mistake review,
subsumption, ranking, quizzes, behavioral enforcement) are added.

Legend: ✅ done · 🔨 this phase · ⏳ later phase · ♻️ reuse existing Anki

---

## What already exists (baseline)

- ✅ Landing page `/ankidote`, tab + toolbar entry (`qt/aqt/toolbar.py`, `main.py`).
- ✅ Diagnostic CAT/IRT engine in `pylib/anki/ankidote/` (`irt`, `engine`,
  `runner`, `item_bank`, `scores`) + diagnostic UI `/ankidote/diagnostic`.
- ✅ Goal + commitment screen `/ankidote/goal` (time-to-target methodology).
- ✅ Stats dashboard `/ankidote/stats` (score range, per-topic, Memory /
  Performance / Readiness beakers with give-up rule).
- ✅ Persistence + sync: `col.set_config("ankidote", …)` via
  `ankidoteStateGet` / `ankidoteStateSet` endpoints (`qt/aqt/mediasrv.py`,
  `ts/routes/ankidote/state.ts`).

---

## Feature map (PRD → plan)

| PRD §   | Feature                                          | Reuse / New                                                           | Phase     |
| ------- | ------------------------------------------------ | --------------------------------------------------------------------- | --------- |
| 3.1     | Topic confidence survey                          | New Svelte + seeds diagnostic θ (engine already accepts `confidence`) | ⏳ P3     |
| 3.2     | Goal screen                                      | ✅                                                                    | done      |
| 3.3     | Commitment list + strictness                     | ✅ (strictness ⏳)                                                    | done/P4   |
| 4.x     | CAT diagnostic                                   | ✅                                                                    | done      |
| 4.3     | Score ranges / ANTIcipated                       | ✅ (dashboard)                                                        | done      |
| **5.1** | **Topic selection (lowest-scoring)**             | New (`planner.py`) over persisted θ                                   | **🔨 P1** |
| 5.2     | Subsumption gate (pre-quiz + framework)          | New                                                                   | ⏳ P3     |
| **5.3** | **Flashcards via SRS**                           | ♻️ Anki decks + FSRS + reviewer                                        | **🔨 P1** |
| 5.3     | Points-at-stake queue ordering                   | New Rust review-order + proto                                         | ⏳ P2     |
| 5.4     | Mistake review (highlight→correct→explain)       | New                                                                   | ⏳ P4     |
| **5.5** | **2–3 estimate-adjustment problems / ~30 cards** | New (`problems.py`, temp) + reuse `CatSession`                        | **🔨 P1** |
| 5.6     | Bounded interleaving                             | New (selection policy)                                                | ⏳ P3     |
| 5.7     | Sub-topic mastery quiz → score update            | Partial in P1 (score update), quiz ⏳ P3                              | 🔨/⏳     |
| 6.x     | Behavioral enforcement                           | New                                                                   | ⏳ P4     |
| 7.2     | Points-at-stake Rust/proto                       | New                                                                   | ⏳ P2     |
| 7.4     | Item/problem calibration pipeline                | Offline, later                                                        | ⏳ P5     |

---

## Phase 1 — the basic loop (this phase) 🔨

Goal: `pick lowest-scoring topic → study its Anki cards → 2–3 practice problems
→ re-estimate θ → update score range → next topic`. No mistake review, no
subsumption, no ranking, no quizzes yet.

### 1.1 Topic model + selection — `pylib/anki/ankidote/topics.py`

- Canonical GMAT topic tree (section → topics), section weights.
- Derive the source of truth for topics from the item bank today; taxonomy is
  data so it can grow.
- `select_topic(state)`: from the persisted per-topic θ/score (the diagnostic
  result stored in config), pick the topic whose section-weighted score range
  is **furthest below target** (PRD §5.1). Falls back to lowest-confidence /
  first untested topic when no θ exists.

### 1.2 Per-topic exam-problem storage — `pylib/anki/ankidote/problems.py`

- **Separate store from the diagnostic item bank** (PRD calls problems distinct
  from cards; also distinct from diagnostic items).
- For now **generate temp problems per topic** deterministically (seeded), each
  with IRT params so they can drive θ re-estimation via the existing
  `CatSession`. Real authored/calibrated problems drop in later behind the same
  interface (`get_problems(topic, n, seed)` / `get(problem_id)`).
- Stored per-topic; content kept out of the diagnostic bank.

### 1.3 Cards from Anki decks ♻️ — reuse decks + reviewer

- Map each topic → an Anki deck (`Ankidote::<Section>::<Topic>`), created on
  demand (`col.decks.id(name, create=True)`); stored in config
  (`ankidote.deckMap`). Real user cards live in these decks.
- Studying = the **native Anki flow**: select the deck and `moveToState(
  "overview")` (reuse reviewer/FSRS/scheduler untouched). Triggered by a bridge
  command from the loop page. Card-count-aware "return after ~30" is a P2
  refinement (needs reviewer instrumentation / points-at-stake); P1 lets the
  student study then continue to problems.

### 1.4 Loop engine (Python) — `pylib/anki/ankidote/loop.py`

- Holds the outer-loop state for a session: current topic, phase
  (`cards` → `problems` → `update`), a `CatSession` seeded from the topic's
  stored θ, and the running score.
- `record_problem(id, choice)` grades via `Item.is_correct`, re-estimates θ
  (reuse `engine`/`irt`), writes the updated per-topic θ/score back into the
  persisted config so the dashboard + selection see it.

### 1.5 Endpoints (`qt/aqt/mediasrv.py`) + client

- `ankidoteLoopState` → next topic, phase, deck name, and score.
- `ankidoteLoopProblems` → the 2–3 problems for the current topic (temp).
- `ankidoteLoopAnswer` → grade one, return updated θ/score/phase.
- Reuse the `ankidote` config blob for persisted θ/score; whitelist the new
  endpoints (main webview, like the diagnostic ones).
- Bridge command `ankidote:study:<topic>` handled in
  `main.py::_ankidote_link_handler` to launch the topic's deck.

### 1.6 Loop UI — `ts/routes/ankidote/loop/+page.svelte`

- Shows current topic + its score range, section weight, and why it was picked.
- Phase `cards`: "Study N flashcards in <deck>" → bridge command launches the
  deck; "I've finished my cards → problems".
- Phase `problems`: serve the 2–3 problems, grade each, show correctness.
- Phase `update`: show the updated score range and the next topic; loop.
- Entry from the dashboard ("Start studying") and after locking in the plan.

### Phase 1 acceptance

- From a completed diagnostic, the loop picks a sensible weak topic, opens a
  real Anki deck for cards, serves temp problems, and the per-topic + overall
  score range visibly updates and persists/syncs.

---

## Phase 2 — points-at-stake + card-count integration ⏳

- Proto `PointsAtStakeOrder` + Rust review-order variant (PRD §7.2); Python
  computes `topic_weight × student_weakness` and calls the new RPC.
- Reviewer instrumentation to auto-return to the loop after ~30 cards.

## Phase 3 — subsumption, survey, interleaving, quizzes ⏳

- Confidence survey `/ankidote/survey` (seeds θ).
- Subsumption gate: pre-cards quiz + framework page when topic < floor X.
- Bounded sub-topic interleaving; sub-topic mastery quiz → score update.

## Phase 4 — friction features ⏳

- Mistake review (highlight → correct → explain), mistake taxonomy.
- Answer-choice ranking UI (diagnostic + drills).
- Self-evaluation, organize, note-to-self, fact interrogation, strictness,
  scheduling enforcement.

## Phase 5 — calibration ⏳

- Offline IRT calibration pipeline for items + problems (`py-irt`/`girth`).
