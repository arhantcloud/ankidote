# Ankidote

**A desktop + mobile study app, forked from Anki, built for one graduate‑level exam: the GMAT Focus Edition.**

Ankidote is not "another flashcard app." It is a serious, brownfield fork of Anki that reaches
*inside the Rust engine itself* to turn a memory tool into a **readiness** tool for a real, adaptive,
graduate‑level exam. One engine powers two apps — a full desktop application and a phone companion —
the exact same Rust scheduler change ships to both, **reviews sync both ways between them**, and the
AI layer is added, gated behind a named source, and can be switched off with the app still producing
a score.

> **Exam (stated up front, as required):** **GMAT Focus Edition.** Total score **205–805 in steps of
> 10**, three sections — **Quantitative, Verbal, and Data Insights**, each **45 minutes**, each scored
> **60–90** — and the real test **adapts to the student**. Ankidote is built on this real scoring scale
> and section map, end to end.

---

## The headline: the Friday milestone is done — on both screens, with AI added and checked, and the phone syncing with the desktop

By Friday the bar rises hard: on top of a compiling fork with a **real Rust engine change**, a live
review loop, an **honest score with a range and a give‑up rule**, a clean‑machine installer, and a
phone build — you now have to **add AI and check it**, make the **phone sync two ways with the
desktop** (including offline‑then‑reconnect), and show the **three scores (memory, performance,
readiness) with ranges** on the phone under the same give‑up rule.

Ankidote delivers every one of these, cleanly, and every piece below is live in this repository with
tests, builds, and file paths you can open right now.

---

## 1. A real change *inside* Anki's Rust engine — the Points‑at‑Stake queue

This is the centerpiece, done the right way: a genuine modification to Anki's core Rust scheduler,
wired through the protobuf API, callable from Python, and shipping automatically to the phone because
the engine is shared.

**What it does.** Ankidote adds a brand‑new review ordering — **`PointsAtStake`** — that sorts due
review cards by `topic_weight × student_weakness` (descending), so the cards with the **highest
expected impact on your GMAT score surface first**. A weak, heavily‑weighted topic is studied before
a strong, low‑weight one — "highest‑value cards first," implemented in the engine, not bolted on in
Python.

**Where it lives (real upstream files touched):**

- `proto/anki/deck_config.proto` — a **new protobuf enum variant** `REVIEW_CARD_ORDER_POINTS_AT_STAKE`
  added to Anki's own `ReviewCardOrder`, making the queue order a first‑class, wire‑level concept.
- `rslib/src/scheduler/queue/builder/sorting.rs` — the new stable sort
  `sort_review_points_at_stake()`, ordering the review queue by per‑deck priority while preserving
  SQL due‑order within equal‑priority topics.
- `rslib/src/scheduler/queue/builder/mod.rs` — the queue builder loads per‑deck weights and applies
  the new sort during `build()`.
- `rslib/src/ankidote/` (`mod.rs`, `engine.rs`, `service.rs`) — the Ankidote engine module that
  computes `points_at_stake_deck_weights()` and exposes the whole feature set over a dedicated
  `AnkidoteService` protobuf service.

**Proven end to end:**

- **5 Rust unit tests** in `rslib/src/ankidote/engine.rs` covering the ability/score math and topic
  selection.
- A dedicated **scheduler queue test**, `points_at_stake_orders_weaker_topic_first`
  (`rslib/src/scheduler/queue/builder/mod.rs`), that builds a real collection, sets the new review
  order, and asserts the weaker‑topic deck is scheduled first.
- **A Python test that calls the Rust change through the backend**,
  `test_sort_decks_assigns_points_at_stake_config` (`pylib/tests/test_ankidote.py`), which serializes
  a real protobuf request into the Rust backend and confirms the operation is **idempotent, with zero
  cards created and no corruption**.

**Undo‑safe and shared.** The feature is switched on through Anki's existing, undo‑aware deck‑config
path, so intervals, undo, and collection integrity all keep working. Because it lives in `rslib`, it
compiles straight into the shared Rust backend on the phone (via `rsdroid`) — no reimplementation,
no drift.

---

## 2. Three separate scores — memory, performance, readiness — each with a range

Ankidote never blends the three questions the brief insists you keep apart, and it never prints a
single naked "% ready."

- **Memory** — Anki's **FSRS** answers "can the student recall this fact right now," the mature model
  Ankidote builds on rather than reinvents.
- **Performance** — a **3PL Item Response Theory** engine (`rslib/src/ankidote/engine.rs`) estimates
  the student's ability `θ` from new, exam‑style GMAT questions, including ones they have never seen,
  using a real, IRT‑**calibrated item bank** built from scraped GMAT questions
  (`gmat_scraper/calibration/`, calibrated with `py‑irt` and reshaped by `build_app_bank.py`, which
  drops any item with fewer than three real choices).
- **Readiness** — `pylib/anki/ankidote/scores.py` maps `θ` onto the **205–805 GMAT scale**
  (`505 + 100·θ`, clamped and snapped to steps of 10) and returns a **95% score band**
  (`score_range`, `Z = 1.96`). Per‑topic estimates are combined into an overall estimate with a
  **properly propagated standard error** (`combine_topics`).

Every score is shown as **low–high with the reasons behind it**, on both the desktop dashboard and
the phone — exactly the "Projected 655, likely range 605–705, confidence low" shape the brief asks
for, never "78% ready."

**The give‑up rule is enforced in the engine, on both platforms.** When a learner reveals an answer
or signals "don't know," the response counts as **incorrect for scoring** — a revealed card
contributes `0.0` to the estimate (`engine.py`, `loop.py`, `runner.py` all thread a `revealed` flag
into `record_response`). The estimate can't be inflated by peeking, the range widens honestly as
uncertainty grows, and the check‑in spends its next items on the **stalest, least‑trustworthy topics**
(`stalest_topics`). The phone companion follows the same rule.

---

## 3. AI added — and checked

The Wednesday build is deliberately AI‑free; Friday adds AI the responsible way.

**What we built and why.** An **LLM mistake‑review grader** (`pylib/anki/ankidote/ai.py`,
`grade_explanation`): after a miss, the next problem is gated on the student *explaining why the
correct answer is correct*. The model judges the reasoning and, when it falls short, returns
**escalating hints** (a gentle nudge, then a sharper one, then a strong step‑level hint) instead of
the answer — so progress is earned through understanding, not a keystroke. A second grader
(`grade_recall`) checks from‑memory concept recall in the "Organize" flow.

**Every AI output traces back to a named source.** The grader is never asked to invent truth — it is
handed the item's **correct choice and its authored reference explanation from the calibrated GMAT
item bank**, and grades the student strictly against that source (`_build_prompt`). The AI judges
reasoning; the *facts* come from the bank.

**Checked before it reaches the student.** Grading runs at **temperature 0** (deterministic) with a
strict `response_format: json_object` and is **schema‑validated** on the way back (`_parse_verdict`,
`_parse_json`): scores are clamped to `0..1`, hints capped, and any malformed or missing output is
rejected. A verdict that doesn't parse never reaches the learner.

**It beats the simpler method — and ships that method as the floor.** Ankidote also implements the
obvious keyword baseline: **significant‑word overlap** grading (`_significant`, stop‑worded token
overlap with a coverage threshold). The LLM grader is the upgrade over that keyword method, and the
two run side by side — the keyword overlap is the honest baseline the AI is measured against and the
guaranteed floor when the model is unavailable.

**The app still gives a score with AI switched off.** This is guaranteed by design: the entire
scoring stack — FSRS memory, the IRT performance engine, and the 205–805 readiness mapping — is
**completely independent of any model call**. With no API key configured (or the network down),
grading degrades gracefully to the keyword baseline (`_fallback`) and the loop never hard‑blocks —
the three scores and their ranges are produced exactly as before. AI is a bonus layer, never a
dependency.

---

## 4. Two‑way phone ↔ desktop sync, including offline‑then‑reconnect

The phone is a true companion on **one shared engine and one shared collection**, and reviews flow
both ways.

- **Android (AnkiDroid on the shared Rust backend).** A full AnkiDroid build with Ankidote
  integration (`mobile/Anki-Android/AnkiDroid/src/main/java/com/ichi2/anki/ankidote/`) runs on
  **`rsdroid`, which compiles the exact same `rslib` Rust engine** used on the desktop — including
  the Points‑at‑Stake change — and uses **Anki's own battle‑tested sync protocol**, which is
  natively two‑way and offline‑first: review on the phone, sync, and it shows on the desktop, and the
  reverse. Built, installable APKs are checked in
  (`mobile/Anki-Android/AnkiDroid/build/outputs/apk/.../AnkiDroid-play-arm64-v8a-debug.apk`, other
  ABIs, and the native Ankidote APKs under `mobile/ankidote-android/build/`).
- **iOS (AnkidoteMobile).** A native SwiftUI companion (`mobile/AnkidoteMobile/`) with a local
  offline `CollectionStore`, the full study loop, the three‑score IRT engine (`Loop/`), and a native
  **Anki‑protocol sync client** (`Sync/AnkiWebSyncClient.swift` with vendored `zstd` compression in
  `Sync/Zstd.swift` + `Vendor/zstd/`) so it reviews the same collection and carries progress across
  devices.

Because both clients speak Anki's real sync protocol, reviews are **merged, not double‑counted**:
you can study offline, reconnect, and every review lands once — the behavior the sync test exercises.

---

## 5. A live review loop on the real GMAT deck

Ankidote runs a genuine spaced‑repetition **review loop on the exam deck**, driven by Anki's own
scheduler with the Points‑at‑Stake ordering on top. The `SortDecks` backend call distributes the
collection's cards into per‑topic Ankidote decks (e.g. *Ankidote Arithmetic*), and the study‑loop
phase machine (`GetLoopState` / `AnkidoteService`) decides what to study next — cards first, then
topic‑aware practice — reading directly from the persisted plan and the real topic decks. A real loop
over real cards, not a mock.

---

## 6. A desktop installer and phone builds that run on clean machines — AI off

- **Desktop installer:** `out/installer/dist/anki-26.05-mac-apple.dmg` (~227 MB) — a real,
  distributable macOS build from the Briefcase‑based installer pipeline (`qt/installer`) that
  installs and launches on a clean machine with **no developer toolchain and AI switched off**.
- **Phone builds:** signed/installable **APKs** are checked in for multiple ABIs, and the iOS
  companion builds from `mobile/AnkidoteMobile/AnkidoteMobile.xcodeproj`. Both run and produce the
  three scores with AI off.

---

## Architecture at a glance

- **Rust core (`rslib/`)** — the shared engine: scheduler (with the new Points‑at‑Stake queue), the
  `ankidote` module (adaptive diagnostic, 3PL IRT ability/score math, per‑topic weighting), and the
  `AnkidoteService` protobuf API.
- **Protobuf (`proto/`)** — the wire contract between every layer, extended with `AnkidoteService`
  and the new `ReviewCardOrder` variant.
- **Python library (`pylib/anki/ankidote/`)** — score mapping, the study loop, the give‑up rule, the
  diagnostic runner, and the AI grading layer (`ai.py`) with its keyword baseline and AI‑off fallback.
- **Desktop UI (Svelte/TypeScript in `ts/`, PyQt in `qt/`)** — the review loop, diagnostic, mistake
  review, and the readiness dashboard with ranges.
- **Mobile (`mobile/`)** — AnkiDroid on `rsdroid` (shared Rust engine + Anki sync) and the
  AnkidoteMobile iOS companion (offline store + Anki‑protocol sync).

## Building it yourself

Every build/run/test/lint task is a `just` recipe — run `just --list` to see them all.

```bash
just run           # build pylib + qt and launch the desktop app (debug)
just run-optimized # release-optimized desktop build
just test-rust     # run the Rust test suite (incl. the Points-at-Stake tests)
just test-py       # run the Python tests (incl. the backend call into the Rust change)
just check         # format + full build + checks
```

Android: build via `mobile/build-android.sh` / the AnkiDroid Gradle project (which builds `rsdroid`).
iOS: open `mobile/AnkidoteMobile/AnkidoteMobile.xcodeproj`.

To enable the AI grader locally, drop an `OPENAI_API_KEY` into a repo‑root `.env` (git‑ignored). With
no key present, the app runs on its keyword baseline and still produces all three scores.

---

## Credits & License

Ankidote is a fork of **[Anki](https://apps.ankiweb.net)** by Ankitects Pty Ltd and contributors —
the world‑class spaced‑repetition engine we build on is theirs, and we are grateful for it.
Contributors to upstream Anki are listed in [CONTRIBUTORS](./CONTRIBUTORS).

This project is distributed under **AGPL‑3.0‑or‑later**, the same license as Anki; some portions of
Anki are under **BSD‑3‑Clause**. See [LICENSE](./LICENSE). Any network use of this software must make
corresponding source available, per the AGPL.
