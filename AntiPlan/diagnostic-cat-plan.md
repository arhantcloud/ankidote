# Diagnostic engine plan — `catsim` + `py-irt`

Implements PRD §4 (Diagnostic) and §7.3/§7.4 (CAT/IRT engine, item bank
calibration). Everything below marked **verified** was actually run against
`catsim 0.21.0` and `py-irt 0.7.1` on Python 3.13.

---

## 1. Division of labor between the two libraries

| Concern                                                 | Library  | When it runs                                                                                  |
| ------------------------------------------------------- | -------- | --------------------------------------------------------------------------------------------- |
| Item selection, θ estimation, stopping, θ→score mapping | `catsim` | **Runtime**, inside the app, during the diagnostic / mini-CATs / estimate-adjustment problems |
| Item parameter calibration (a, b from response data)    | `py-irt` | **Offline pipeline only** — never shipped in the app                                          |

This split is forced by dependencies, not just taste: `py-irt` pulls in
PyTorch + Pyro (~85 MB torch wheel alone), while `catsim` needs only
numpy/scipy (already in Anki's build) plus matplotlib/numexpr. `catsim` also
explicitly does not do calibration, and `py-irt` does not do adaptive
selection — they are two halves of one system.

## 2. What was learned about `catsim` (verified)

### 2.1 Use the components standalone, not the `Simulator`

`catsim`'s README leads with `Simulator`, which simulates fake examinees.
For a real interactive test we drive the four components manually. All four
accept keyword args and work without a `Simulator` (verified):

```python
from catsim import ItemBank
from catsim.initialization import FixedPointInitializer
from catsim.selection import MaxInfoSelector
from catsim.estimation import NumericalSearchEstimator
from catsim.stopping import MinErrorStopper
from catsim import irt

bank = ItemBank(param_matrix)            # (n, 3) ndarray of a, b, c
initializer = FixedPointInitializer(theta0)   # survey-seeded prior
selector = MaxInfoSelector()
estimator = NumericalSearchEstimator()
stopper = MinErrorStopper(0.35, min_items=8, max_items=25)

est_theta = initializer.initialize()
administered, responses = [], []
while not stopper.stop(administered_items=bank.items[administered], theta=est_theta):
    idx = selector.select(item_bank=bank, administered_items=administered,
                          est_theta=est_theta)
    if idx is None:            # bank exhausted
        break
    answered_right = present_item_to_student(idx)      # our UI
    administered.append(idx)
    responses.append(answered_right)
    est_theta = estimator.estimate(item_bank=bank,
                                   administered_items=administered,
                                   response_vector=responses,
                                   est_theta=est_theta)
se = irt.see(est_theta, bank.items[administered])
```

The `present_item_to_student` step is where the loop suspends and waits for
the webview — see §5.

**API gotchas (verified, will bite otherwise):**

- `selector.select` / `estimator.estimate` take `administered_items` as a
  list of **indices**; `stopper.stop` takes the **parameter rows**
  (`bank.items[administered]`). Inconsistent, easy to pass wrong.
- `response_vector` is a list of **bools**, parallel to `administered_items`.
- `MaxInfoSelector` returns `None` when no eligible item remains.
- `ItemBank` accepts an `(n, 3)` matrix of `(a, b, c)` and pads the
  upper-asymptote column `d = 1` and an exposure-rate column itself
  (final shape `(n, 5)`).

### 2.2 Useful helpers in `catsim.irt`

- `irt.see(theta, items)` — standard error of the estimate; drives the score
  _range_ (θ ± 1.96·SE) that the PRD insists on.
- `irt.theta_to_scale(theta, scale_min=205, scale_max=805, theta_min, theta_max)`
  — linear θ→score mapping. Good enough for v1; PRD §8 already flags
  validating against real score reports as an open task. The calibration
  table from PRD §7.3 replaces this later.
- `irt.icc(theta, a, b, c, d)` — P(correct); used for simulated smoke tests
  and for the "expected impact" numbers in the commitment screen.
- `irt.confidence_interval`, `irt.test_info`, `irt.reliability` also exist.

### 2.3 Sanity run (verified)

A simulated student (true θ = 0.8, survey confidence 4/5 → start θ = 0.75)
against a generated 200-item 3PL bank: SE fell 2.92 → 0.36 in 25 items,
final estimate 0.27 ± 0.36, mapped range 461–602 on a 205–805 scale. The
run confirms the min-SE stopper, warm start, and range mapping behave as the
PRD describes (~25 items per topic-cluster is the right budget expectation).

## 3. What was learned about `py-irt` (verified)

- Models registered: `1pl`, `2pl`, `3pl`, `4pl`, `amortized_1pl`,
  `multidim_2pl`. (README only mentions 1/2/4PL; `3pl` exists in 0.7.1.)
- Input format is sparse jsonlines, one line per student:
  `{"subject_id": "s1", "responses": {"item17": 1, "item23": 0}}`.
  Sparsity matters: our response matrix will be very sparse since each
  student only ever sees the items the CAT chose for them.
- Python API (preferred over CLI so the pipeline can post-process):

```python
from py_irt.dataset import Dataset
from py_irt.config import IrtConfig
from py_irt.training import IrtModelTrainer

dataset = Dataset.from_jsonlines("responses.jsonlines")
config = IrtConfig(model_type="2pl", priors="hierarchical")
trainer = IrtModelTrainer(config=config, data_path=None, dataset=dataset)
trainer.train(epochs=1000, device="cpu")
params = trainer.best_params
# keys: ability, diff, disc, irt_model, item_ids, subject_ids
# item_ids maps position -> item name; diff/disc are position-indexed
```

- **Parameter recovery test (verified):** 500 synthetic students × 60 items,
  2PL, 1000 epochs (~30 s CPU). Difficulty recovered with r = 0.99,
  discrimination r = 0.90. Good.
- **Scale caveat (verified, important):** py-irt is Bayesian VI with its own
  latent scale — recovered discriminations came back in the range 0.2–0.7
  when the true values averaged ~1.0. Only the product a·(θ − b) is
  identified. The calibration pipeline must therefore **standardize**: fit,
  then linearly rescale so the ability distribution is N(0, 1), applying the
  inverse transform to a and b before export. Without this the θ scale
  drifts between calibration runs and the θ→score table breaks.
- 2PL has no guessing parameter. For multiple-choice GMAT items we set
  `c = 1/num_choices` ourselves at export time (verified that a
  `(a, b, 0.2)` matrix feeds straight into `ItemBank` and `MaxInfoSelector`).
  Once real telemetry accumulates we can switch the fit to `3pl` and estimate
  c instead.
- `py_irt.scoring.calculate_theta(difficulties, response_pattern)` exists as
  a lightweight scorer, but we don't need it — catsim's estimator does this
  job at runtime with the full 3PL likelihood.

## 4. Architecture

```
OFFLINE (repo tooling, not shipped)                RUNTIME (shipped app)
┌──────────────────────────────┐        ┌───────────────────────────────────┐
│ item authoring               │        │ ts/routes/ankidote/diagnostic     │
│  + topic/sub-topic tags      │        │   (question UI, ranking UI)       │
│         │                    │        │        ▲ POST /_anki/ankidote/*   │
│         ▼                    │        │        ▼                          │
│ py-irt 2PL fit               │        │ aqt/mediasrv.py routes            │
│  → standardize θ to N(0,1)   │        │        ▼                          │
│  → rescale a, b; set c=1/k   │        │ pylib/anki/ankidote/engine.py     │
│         │                    │        │   CatSession (catsim components)  │
│         ▼                    │        │   per-topic θ/SE, score mapping   │
│ item_bank.json ──────────────┼──────► │        ▼                          │
│  (a, b, c, topic, content)   │        │ points-at-stake RPC → rslib queue │
└──────────────────────────────┘        └───────────────────────────────────┘
        ▲ response telemetry exported from the app feeds recalibration
```

### 4.1 Runtime engine — `pylib/anki/ankidote/`

New package (pure Python, imports `catsim` only):

- `item_bank.py` — loads `item_bank.json` into one `ItemBank` **per topic**
  plus an index of item metadata (topic, sub-topic, question content id).
- `engine.py` — `CatSession`: owns initializer/selector/estimator/stopper
  and the administered/response state, exposes exactly two operations:
  - `next_item() -> ItemRef | None` — returns the next question (or None =
    stopped). Internally: stop-check, then `MaxInfoSelector.select`.
  - `record_response(item_ref, correct: bool, ranking: list[int] | None)` —
    appends to the response vector, re-runs
    `NumericalSearchEstimator.estimate`, recomputes SE.
    The session is synchronous and cheap (<1 ms per step, verified), so it can
    run on the mediasrv request thread — no background workers needed.
- `scores.py` — θ/SE → score-range mapping (`irt.theta_to_scale` for v1,
  calibration table later); combines per-topic θ into section and total
  ranges using the topic weights; persists θ/SE history (PRD §7.6).

Dependency change: add `catsim` to `pylib/pyproject.toml` dependencies
(the engine lives in pylib so future non-Qt frontends can reuse it; `aqt`
already depends on `anki`). `py-irt` is **not** added anywhere — the offline
pipeline gets its own `tools/`-style venv.

### 4.2 Multi-topic diagnostic: parallel per-topic sessions

The GMAT diagnostic must produce **per-topic** θ, but a single
`MaxInfoSelector` over one big bank would happily spend all its items on one
topic. Design: one `CatSession` per topic (bank filtered by topic tag), and a
thin `DiagnosticRunner` that round-robins among sessions, always advancing
the topic whose current SE is highest (greedy max-uncertainty scheduling).
Global stop when every topic's stopper fires or the total item cap (~45 min
budget) is hit. This gives us content balancing for free and keeps each θ a
clean unidimensional estimate — no custom selector subclass needed for v1.

The same `CatSession` class, with different stopper settings, covers all
four PRD use cases:

| Use case                            | Initializer                           | Stopper                                                                   |
| ----------------------------------- | ------------------------------------- | ------------------------------------------------------------------------- |
| Onboarding diagnostic               | survey-seeded `FixedPointInitializer` | `MinErrorStopper(0.35, min_items=8, max_items=25)` per topic + global cap |
| Weekly mini-CAT check-in            | current θ                             | `MinErrorStopper(0.40, max_items=10)`                                     |
| Estimate-adjustment problems (§5.5) | current θ                             | `MinErrorStopper(inf → effectively max_items only, max_items=3)`          |
| Sub-topic mastery quiz              | current θ                             | fixed `max_items=5` on the sub-topic's bank slice                         |

### 4.3 Survey seeding

Confidence slider 1–5 → starting θ via a fixed table
`{1: −1.5, 2: −0.75, 3: 0.0, 4: 0.75, 5: 1.5}` (verified this warm start
works; PRD §3.1). The survey answer is also stored raw so the
confidence-vs-ability gap can be computed after the diagnostic.

### 4.4 Wire protocol (Qt ↔ Svelte)

The diagnostic UI (`ts/routes/ankidote/diagnostic`) talks to the engine via
mediasrv POST handlers (same pattern as existing `/_anki/` endpoints), not
bridge commands — the payloads (question content, ranking responses) are too
structured for `pycmd`. Three endpoints:

- `POST /_anki/ankidote/diagStart` → creates `DiagnosticRunner`, returns
  first item + progress metadata.
- `POST /_anki/ankidote/diagAnswer` `{itemId, correct | ranking[]}` →
  records response, returns next item or the finished flag.
- `POST /_anki/ankidote/diagState` → θ/SE/score ranges per topic (drives the
  live "score range narrowing" display and the final score screen).

Answer-choice ranking (PRD §4.2): v1 scores the item dichotomously for IRT
(rank-1 choice correct or not) and stores the full ranking for the mistake
taxonomy. Partial-credit IRT models are out of scope for catsim; revisit
only if ranking data proves rich enough to justify a custom estimator.

### 4.5 Item bank format (shipped artifact)

`item_bank.json`, produced by the offline pipeline:

```json
{
    "scale": { "theta_min": -3, "theta_max": 3, "calibrated_at": "..." },
    "items": [
        {
            "id": "q-arith-0042",
            "topic": "quant.arithmetic",
            "subtopic": "percents",
            "a": 1.12,
            "b": -0.35,
            "c": 0.2,
            "content_id": "..."
        }
    ]
}
```

Question content itself lives with the card/note storage; the bank carries
only parameters + tags + a content pointer.

## 5. Offline calibration pipeline (`tools/ankidote-calibrate/`)

A standalone script + its own venv (torch/pyro stay out of the app):

1. **Bootstrap (no telemetry yet):** author-assigned difficulty tiers per
   item (easy/medium/hard → b ∈ {−1, 0, 1}), a = 1.0, c = 1/num_choices.
   This is enough for the CAT to function on day one — selection quality
   improves with calibration but nothing breaks without it.
2. **Recalibration (as responses accumulate):** export response telemetry to
   py-irt jsonlines → fit 2PL with hierarchical priors →
   **standardize** (linear-transform so subject abilities are N(0,1), apply
   inverse to a, b) → set c = 1/num_choices → emit `item_bank.json`.
   ~30 s for 500×60 on CPU; scales fine to a realistic bank.
3. **Validation gate before shipping a bank:** run `catsim.Simulator` (its
   actual intended use) over the new bank with a synthetic N(0,1) cohort and
   assert (a) SE reaches the stopper threshold within the item cap for
   ≥ 90 % of simulated examinees, (b) no item's exposure rate exceeds ~0.3.
   This catches thin difficulty coverage per topic before students do.
4. When telemetry volume supports it, switch the fit to `3pl` and estimate
   guessing instead of fixing it.

## 6. Build order

1. **Engine + tests** — `pylib/anki/ankidote/{item_bank,engine,scores}.py`,
   `catsim` dep in `pylib/pyproject.toml`, unit tests with a simulated
   student (the §2.3 harness becomes the test). No UI needed to verify.
2. **Bootstrap bank** — small hand-tagged item set with tier-based
   parameters + the calibration script skeleton that emits `item_bank.json`.
3. **Wire protocol** — the three mediasrv endpoints; `DiagnosticRunner`
   session held on the collection/state object.
4. **Diagnostic UI** — `ts/routes/ankidote/diagnostic`: question rendering,
   ranking interaction, progress + live SE display, final score-range screen.
5. **Score plumbing** — persist θ/SE history (PRD §7.6), compute
   `student_weakness` from θ distance-to-target, feed the points-at-stake
   RPC (PRD §7.2) after each session.
6. **Calibration loop** — telemetry export + py-irt recalibration +
   Simulator validation gate.

Steps 1–2 are pure Python and independently testable; the UI (step 4) can
develop against a stub endpoint returning canned items in parallel.

## 7. Risks / open questions

- **θ→GMAT score mapping** is linear-for-now; already flagged in the PRD as
  needing validation against real score reports. Keep the mapping isolated
  in `scores.py` so swapping in a lookup table is one-file change.
- **py-irt scale drift** between recalibrations: mitigated by the
  standardization step; also anchor by re-fitting with all historical
  responses rather than incremental fits, so the scale is re-derived from
  the same population each time.
- **Small per-topic banks** early on will exhaust under `MaxInfoSelector`
  for repeat testing (mini-CATs exclude already-seen items). The exposure
  check in the validation gate plus per-student "seen item" exclusion lists
  need real content volume — flag topics whose bank drops below ~3× the
  per-topic item cap.
- **catsim is unidimensional.** Per-topic sessions sidestep this, but topic
  θs are estimated independently even though abilities correlate. If
  diagnostics feel too long, `multidim_2pl` in py-irt plus a custom
  cross-topic prior is the future path — explicitly not v1.
