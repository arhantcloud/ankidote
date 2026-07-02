<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    // PRD §3.2 (desired score × time budget) and §3.3 (commitment list).
    // Every commitment is friction the app will enforce; opting out never
    // lowers the target — it visibly raises the required time and lowers the
    // ANTIcipated score. The estimate model here is intentionally simple and
    // client-side for now (the PRD flags these magnitudes as illustrative,
    // to be replaced by workload-simulation telemetry).
    import { goto } from "$app/navigation";
    import { onMount } from "svelte";
    import { loadAnkidoteState, saveAnkidoteState } from "../state";

    interface RateOption {
        level: string;
        // reps/hour headline for the button
        rate: number;
        // how much more (or less) each study hour is worth at this pace
        mult: number;
        // the study environment required to sustain this pace
        env: string;
    }

    interface Commitment {
        id: string;
        name: string;
        enforce: string;
        tradeoff: string;
        cost: string;
        enabled: boolean;
        // fraction of the study-hours gap this habit shaves off by making
        // each hour more effective (learning-science efficiency). Only the
        // non-throughput habits carry this.
        efficiency?: number;
        // throughput commitments (cards/problems) pick a per-hour pace.
        unit?: string;
        rates?: RateOption[];
        selected?: string;
    }

    let phase: "goal" | "plan" = "goal";

    let desiredScore = 645;
    let weeklyBudget = 8;
    let testDate = defaultTestDate();

    // Baseline (point estimate) from the diagnostic that ran first. Falls back
    // to the middle of the scale if the user reached this screen directly.
    const FALLBACK_BASELINE = 505;
    let baseline = FALLBACK_BASELINE;
    let hasDiagnostic = false;
    let diagLow = 0;
    let diagHigh = 0;

    function applyDiagnostic(d: {
        baseline?: number;
        low?: number;
        high?: number;
    }): boolean {
        if (typeof d?.baseline === "number") {
            baseline = d.baseline;
            diagLow = d.low ?? d.baseline;
            diagHigh = d.high ?? d.baseline;
            hasDiagnostic = true;
            // Default the target a bit above the measured baseline.
            desiredScore = toScore(Math.min(baseline + 100, 805));
            return true;
        }
        return false;
    }

    onMount(async () => {
        // Prefer the persisted/synced diagnostic; fall back to the in-session
        // sessionStorage copy if the backend has nothing yet.
        const state = await loadAnkidoteState();
        if (state.diagnostic && applyDiagnostic(state.diagnostic)) {
            return;
        }
        try {
            const raw = sessionStorage.getItem("ankidote.diagnostic");
            if (raw) {
                applyDiagnostic(JSON.parse(raw));
            }
        } catch {
            // Ignore; fall back to defaults.
        }
    });

    let commitments: Commitment[] = [
        {
            id: "mistakes",
            name: "Mistake review",
            enforce: "Walk every miss: highlight → correct → explain why.",
            tradeoff:
                "Errors corrected with feedback are the single strongest predictor of not repeating them (Metcalfe 2017).",
            cost: "+6 min / session",
            enabled: true,
            efficiency: 0.12,
        },
        {
            id: "cards",
            name: "Flashcards per hour",
            enforce: "Spaced-retrieval reps, ordered by points at stake.",
            tradeoff:
                "Spaced retrieval roughly doubles long-term retention vs. restudy (Roediger & Karpicke 2006).",
            cost: "",
            enabled: true,
            unit: "cards / hr",
            selected: "focused",
            rates: [
                {
                    level: "relaxed",
                    rate: 30,
                    mult: 0.9,
                    env: "Couch review — music on, phone nearby, easy pace.",
                },
                {
                    level: "focused",
                    rate: 60,
                    mult: 1.0,
                    env: "Quiet desk — phone in another room, steady rhythm.",
                },
                {
                    level: "intense",
                    rate: 100,
                    mult: 1.12,
                    env: "Timed sprints — full silence, rapid-fire recall.",
                },
            ],
        },
        {
            id: "problems",
            name: "Practice problems per hour",
            enforce: "IRT-selected problems to keep your estimate fresh.",
            tradeoff:
                "Without a steady problem cadence your ability estimate goes stale and targeting degrades.",
            cost: "",
            enabled: true,
            unit: "problems / hr",
            selected: "focused",
            rates: [
                {
                    level: "relaxed",
                    rate: 5,
                    mult: 0.9,
                    env: "Untimed — notes open, working to learn, not race.",
                },
                {
                    level: "focused",
                    rate: 10,
                    mult: 1.0,
                    env: "Loosely timed — quiet room, ~6 min per problem.",
                },
                {
                    level: "intense",
                    rate: 18,
                    mult: 1.12,
                    env: "Test-timed — full sim, ~3 min each, no breaks.",
                },
            ],
        },
        {
            id: "ranking",
            name: "Answer-choice ranking",
            enforce: "Rank all five choices instead of picking one.",
            tradeoff:
                "Forces discrimination between distractors and exposes why wrong answers tempt you (Little & Bjork 2015).",
            cost: "+20 s / question",
            enabled: true,
            efficiency: 0.05,
        },
        {
            id: "checkins",
            name: "Diagnostic check-ins",
            enforce: "Periodic mini-CAT to re-anchor your score range.",
            tradeoff:
                "Skipping it means your ANTIcipated score is a guess, not a measurement.",
            cost: "+25 min / week",
            enabled: true,
            efficiency: 0.03,
        },
        {
            id: "explain",
            name: "Explain (self-explanation)",
            enforce: 'Type a one-line "why" on flagged cards.',
            tradeoff:
                "Self-explanation reliably improves transfer to new problems (Chi et al. 1994).",
            cost: "+15 s / card",
            enabled: true,
            efficiency: 0.06,
        },
        {
            id: "organize",
            name: "Organize",
            enforce: "Weekly prompt to restructure your notes and error log.",
            tradeoff:
                "Organizing and transforming material is among the top self-regulated-learner behaviors (Zimmerman & Martinez-Pons 1986).",
            cost: "+10 min / week",
            enabled: true,
            efficiency: 0.03,
        },
        {
            id: "noteToSelf",
            name: "Note-to-self",
            enforce: "Leave a tip on any card you miss twice.",
            tradeoff: "Your future self reads it right before the next attempt.",
            cost: "+10 s / lapse",
            enabled: true,
            efficiency: 0.02,
        },
    ];

    // Which card's reasoning popover is open (only one at a time).
    let openInfoId: string | null = null;
    function toggleInfo(id: string): void {
        openInfoId = openInfoId === id ? null : id;
    }

    // Flashcards/problems keep their own full-width cards (they carry quota
    // sliders); everything else is a compact toggle grid.
    $: quotaCommitments = commitments.filter((c) => c.rates);
    $: gridCommitments = commitments.filter((c) => !c.rates);

    function setLevel(c: Commitment, level: string): void {
        c.selected = level;
        commitments = commitments;
    }

    function defaultTestDate(): string {
        const d = new Date();
        d.setDate(d.getDate() + 70);
        return d.toISOString().slice(0, 10);
    }

    function round1(n: number): number {
        return Math.round(n * 10) / 10;
    }

    function toScore(n: number): number {
        const clamped = Math.min(Math.max(n, 205), 805);
        return 205 + Math.round((clamped - 205) / 10) * 10;
    }

    // Rough GMAT total-score percentile, interpolated between anchor points.
    const PCT: [number, number][] = [
        [205, 1],
        [405, 11],
        [505, 27],
        [555, 42],
        [605, 58],
        [645, 69],
        [705, 86],
        [755, 96],
        [805, 100],
    ];
    function percentile(score: number): number {
        for (let i = 1; i < PCT.length; i++) {
            const [s0, p0] = PCT[i - 1];
            const [s1, p1] = PCT[i];
            if (score <= s1) {
                const t = (score - s0) / (s1 - s0);
                return Math.round(p0 + t * (p1 - p0));
            }
        }
        return 100;
    }

    // --- methodology -------------------------------------------------------
    // Time-to-target is anchored to the diagnostic. We model the study hours
    // needed to reach a score with a convex "effort curve": each point gets
    // more expensive the higher you climb (diminishing returns near the top).
    //
    //   E(s) = A·(s−205) + C·(s−205)²      (cumulative hours from the floor)
    //
    // Hours to go from baseline B to desired D is E(D) − E(B), which is 0
    // when D ≤ B (you're already there). Kept habits make each hour count
    // for more, shaving a fraction off that gap; cards/problems set your
    // daily volume, which drives weekly pace and the ANTIcipated score.
    const EFFORT_A = 0.225;
    const EFFORT_C = 0.00075;
    function effort(score: number): number {
        const x = Math.max(0, score - 205);
        return EFFORT_A * x + EFFORT_C * x * x;
    }
    // Inverse of effort(): the score reachable for a given hour budget.
    function scoreForEffort(hours: number): number {
        const h = Math.max(0, hours);
        const x =
            (-EFFORT_A + Math.sqrt(EFFORT_A * EFFORT_A + 4 * EFFORT_C * h)) /
            (2 * EFFORT_C);
        return 205 + x;
    }

    // --- live plan estimate ------------------------------------------------
    $: weeksLeft = Math.max(
        1,
        Math.round(
            (new Date(testDate).getTime() - Date.now()) / (7 * 24 * 3600 * 1000),
        ),
    );

    $: cardsC = commitments.find((c) => c.id === "cards");
    $: problemsC = commitments.find((c) => c.id === "problems");
    // How much each study hour is worth given the chosen per-hour pace.
    $: cardMult =
        cardsC?.rates?.find((r) => r.level === cardsC?.selected)?.mult ?? 1;
    $: problemMult =
        problemsC?.rates?.find((r) => r.level === problemsC?.selected)?.mult ?? 1;
    $: throughputMult = (cardMult + problemMult) / 2;

    // Raw study-hours gap from the measured baseline to the desired score.
    $: gapHours = Math.max(0, effort(desiredScore) - effort(baseline));
    // Kept habits each shave a share off that gap (efficiency); dropping one
    // gives the hours back. Capped so we never promise a free ride.
    $: keptSavings = Math.min(
        0.6,
        commitments.reduce((s, c) => s + (c.enabled ? (c.efficiency ?? 0) : 0), 0),
    );
    $: totalHours = Math.round(gapHours * (1 - keptSavings));

    // Weeks to finish, at your pace-adjusted weekly hours, vs. weeks to exam.
    $: effectiveWeekly = weeklyBudget * throughputMult;
    $: weeksNeeded =
        totalHours > 0 ? Math.ceil(totalHours / effectiveWeekly) : 0;
    $: onTrack = weeksNeeded <= weeksLeft;

    // Predicted score by the exam: run the hours you'll actually invest
    // (weekly time × weeks left), scaled by your per-hour pace, up the effort
    // curve from the diagnostic baseline. Reported as a range whose width
    // grows with how far we're extrapolating.
    $: investedHours = Math.max(0, weeklyBudget * weeksLeft * throughputMult);
    $: predictedCenter = scoreForEffort(effort(baseline) + investedHours);
    $: predictedMargin = Math.min(
        70,
        Math.max(20, Math.round((20 + (predictedCenter - baseline) * 0.08) / 5) * 5),
    );
    $: predictedLow = toScore(predictedCenter - predictedMargin);
    $: predictedHigh = toScore(predictedCenter + predictedMargin);
    $: alreadyThere = totalHours <= 0;

    function toPlan(): void {
        phase = "plan";
    }

    async function savePlan(): Promise<void> {
        // Persist the plan to the collection config (saves + syncs), keeping a
        // sessionStorage copy for the same-session fast path.
        const plan = {
            baseline,
            desiredScore,
            weeklyBudget,
            testDate,
            totalHours,
            weeksNeeded,
            predictedLow,
            predictedHigh,
            commitments: commitments.map((c) => ({
                id: c.id,
                enabled: c.enabled,
                pace: c.selected,
            })),
        };
        try {
            sessionStorage.setItem("ankidote.plan", JSON.stringify(plan));
        } catch {
            // sessionStorage may be unavailable; the plan is not critical yet.
        }
        await saveAnkidoteState({ plan });
        goto("/ankidote/stats");
    }
</script>

<main class="goal">
    {#if phase === "goal"}
        <section class="panel">
            <span class="eyebrow">Set your target</span>
            <h1>What score, and what's it worth in hours?</h1>
            <p class="sub">
                Pick where you want to land and how much time you'll spend. Your
                required hours are measured up from your diagnostic — so the closer
                you already are, the less it costs.
            </p>

            <div class="baseline" class:estimated={!hasDiagnostic}>
                {#if hasDiagnostic}
                    <span class="baseline-label">Your diagnostic baseline</span>
                    <span class="baseline-value">
                        {baseline}
                        <em>({diagLow}–{diagHigh})</em>
                    </span>
                {:else}
                    <span class="baseline-label">No diagnostic yet</span>
                    <span class="baseline-value">
                        ~{baseline}
                        <em>estimate</em>
                    </span>
                {/if}
            </div>

            <div class="field">
                <div class="field-head">
                    <label for="score">Desired score</label>
                    <span class="field-value">
                        {desiredScore}
                        <em>~{percentile(desiredScore)}th pct</em>
                    </span>
                </div>
                <input
                    id="score"
                    type="range"
                    min="205"
                    max="805"
                    step="10"
                    bind:value={desiredScore}
                />
                <div class="scale">
                    <span>205</span>
                    <span>805</span>
                </div>
            </div>

            <div class="field">
                <div class="field-head">
                    <label for="budget">Time I'll spend</label>
                    <span class="field-value">
                        {weeklyBudget}
                        <em>hrs / week</em>
                    </span>
                </div>
                <input
                    id="budget"
                    type="range"
                    min="2"
                    max="25"
                    step="1"
                    bind:value={weeklyBudget}
                />
                <div class="scale">
                    <span>2 hrs</span>
                    <span>25 hrs</span>
                </div>
            </div>

            <div class="field">
                <div class="field-head">
                    <label for="date">Target test date</label>
                    <span class="field-value">
                        {weeksLeft}
                        <em>weeks away</em>
                    </span>
                </div>
                <input id="date" type="date" bind:value={testDate} />
            </div>

            <div class="actions">
                <button class="cta" on:click={toPlan}>See my plan &rarr;</button>
            </div>
        </section>
    {:else}
        <section class="panel">
            <span class="eyebrow">Your Ankidote plan</span>
            {#if alreadyThere}
                <h1>You're already at {desiredScore}.</h1>
            {:else}
                <h1>{baseline} &rarr; {desiredScore} in {totalHours} hours.</h1>
            {/if}
            <p class="sub compact">
                Hours are measured from your diagnostic. Kept habits shave hours off;
                cards &amp; problems set your pace and ANTIcipated score. Tap
                <span class="inline-i">i</span> for the why.
            </p>

            <div class="stats">
                <div class="stat">
                    <span class="stat-label">Total hours to reach {desiredScore}</span>
                    <span class="stat-value">
                        {totalHours}
                        <em>hrs total</em>
                    </span>
                </div>
                <div class="stat">
                    <span class="stat-label">
                        Predicted in {weeksLeft}
                        {weeksLeft === 1 ? "week" : "weeks"} (by exam)
                    </span>
                    <span class="stat-value accent range">
                        {predictedLow}&ndash;{predictedHigh}
                    </span>
                </div>
                <div class="stat">
                    <span class="stat-label">Time per week</span>
                    <span class="stat-value">
                        <input
                            class="hours-input"
                            type="number"
                            min="1"
                            max="60"
                            step="1"
                            bind:value={weeklyBudget}
                        />
                        <em>hrs / week</em>
                    </span>
                </div>
            </div>

            <p class="verdict" class:warn={!onTrack}>
                {#if alreadyThere}
                    Your diagnostic already meets this target — 0 hours needed. Aim
                    higher to build a plan.
                {:else if onTrack}
                    On track — at {weeklyBudget} hrs/week you'll close the gap in ~{weeksNeeded}
                    {weeksNeeded === 1 ? "week" : "weeks"}, inside your {weeksLeft}-week
                    window.
                {:else}
                    Behind — {totalHours} hrs at {weeklyBudget} hrs/week takes ~{weeksNeeded}
                    weeks, past your {weeksLeft}-week window. Add weekly time, keep more
                    habits, or push the date.
                {/if}
            </p>

            <div class="quota-row">
                {#each quotaCommitments as c (c.id)}
                    <div class="card quota-card">
                        <button
                            class="info"
                            class:active={openInfoId === c.id}
                            aria-label="Why {c.name} matters"
                            on:click={() => toggleInfo(c.id)}
                        >
                            i
                        </button>
                        {#if openInfoId === c.id}
                            <div class="popover">
                                <p class="enforce">{c.enforce}</p>
                                <p class="tradeoff">
                                    {#if c.cost}<b>{c.cost}</b>
                                        &mdash;
                                    {/if}{c.tradeoff}
                                </p>
                            </div>
                        {/if}

                        <div class="card-head">
                            <span class="name">{c.name}</span>
                            <span class="req">required</span>
                        </div>

                        {#if c.rates}
                            <div class="rates">
                                {#each c.rates as r (r.level)}
                                    <button
                                        class="rate"
                                        class:active={c.selected === r.level}
                                        on:click={() => setLevel(c, r.level)}
                                    >
                                        <span class="rate-num">
                                            {r.rate}
                                            <em>{c.unit}</em>
                                        </span>
                                        <span class="rate-env">{r.env}</span>
                                    </button>
                                {/each}
                            </div>
                        {/if}
                    </div>
                {/each}
            </div>

            <div class="grid">
                {#each gridCommitments as c (c.id)}
                    <div class="card" class:off={!c.enabled}>
                        <button
                            class="info"
                            class:active={openInfoId === c.id}
                            aria-label="Why {c.name} matters"
                            on:click={() => toggleInfo(c.id)}
                        >
                            i
                        </button>
                        {#if openInfoId === c.id}
                            <div class="popover">
                                <p class="enforce">{c.enforce}</p>
                                <p class="tradeoff">
                                    {#if c.cost}<b>{c.cost}</b>
                                        &mdash;
                                    {/if}{c.tradeoff}
                                </p>
                            </div>
                        {/if}

                        <div class="card-head">
                            <label class="switch">
                                <input
                                    type="checkbox"
                                    bind:checked={c.enabled}
                                    on:change={() => (commitments = commitments)}
                                />
                                <span class="track"></span>
                            </label>
                            <span class="name">{c.name}</span>
                        </div>

                        {#if c.enabled}
                            <span class="chip on">
                                saves {round1(gapHours * (c.efficiency ?? 0))} hrs
                            </span>
                        {:else}
                            <span class="chip cost">
                                +{round1(gapHours * (c.efficiency ?? 0))} hrs
                            </span>
                        {/if}
                    </div>
                {/each}
            </div>

            <div class="actions split">
                <button class="ghost" on:click={() => (phase = "goal")}>
                    &larr; Adjust target
                </button>
                <button class="cta" on:click={savePlan}>
                    Lock in my plan &rarr;
                </button>
            </div>
        </section>
    {/if}
</main>

<style lang="scss">
    .goal {
        --accent: #45a05a;
        --accent-2: #2e7d46;
        min-height: 100vh;
        display: flex;
        align-items: flex-start;
        justify-content: center;
        padding: 3rem 1.5rem;
        color: var(--fg);
    }

    .panel {
        width: 100%;
        max-width: 46rem;
        border: 1px solid var(--border);
        border-radius: 1.2rem;
        background: var(--canvas-elevated, var(--canvas));
        padding: 2.2rem 2.4rem 2.4rem;
    }

    .eyebrow {
        display: inline-block;
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--accent);
        margin-bottom: 0.8rem;
    }

    h1 {
        font-size: clamp(1.6rem, 4vw, 2.2rem);
        font-weight: 800;
        letter-spacing: -0.02em;
        margin: 0 0 0.8rem;
    }

    .sub {
        font-size: 1rem;
        line-height: 1.6;
        opacity: 0.8;
        margin: 0 0 1.8rem;

        &.compact {
            font-size: 0.9rem;
            margin: 0 0 1rem;
        }
    }

    .inline-i {
        box-sizing: border-box;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.15rem;
        height: 1.15rem;
        aspect-ratio: 1;
        padding: 0;
        border-radius: 50%;
        border: 1px solid var(--border);
        font-style: italic;
        font-family: Georgia, "Times New Roman", serif;
        font-size: 0.7rem;
        font-weight: 700;
        vertical-align: middle;
    }

    /* --- goal inputs --- */
    .baseline {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 0.8rem;
        padding: 0.7rem 1rem;
        margin-bottom: 1.6rem;
        border: 1px solid var(--accent);
        border-radius: 0.8rem;
        background: rgba(69, 160, 90, 0.08);

        &.estimated {
            border-color: var(--border);
            background: transparent;
        }
    }

    .baseline-label {
        font-size: 0.82rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        opacity: 0.8;
    }

    .baseline-value {
        font-size: 1.4rem;
        font-weight: 800;
        letter-spacing: -0.02em;
        color: var(--accent);

        em {
            font-size: 0.8rem;
            font-style: normal;
            font-weight: 600;
            opacity: 0.7;
            color: var(--fg);
        }
    }

    .field {
        margin-bottom: 1.6rem;
    }

    .field-head {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 0.5rem;
    }

    label {
        font-weight: 600;
        font-size: 0.95rem;
    }

    .field-value {
        font-size: 1.3rem;
        font-weight: 800;
        letter-spacing: -0.01em;

        em {
            font-style: normal;
            font-size: 0.8rem;
            font-weight: 600;
            opacity: 0.6;
        }
    }

    input[type="range"] {
        width: 100%;
        accent-color: var(--accent);
        cursor: pointer;
    }

    .scale {
        display: flex;
        justify-content: space-between;
        font-size: 0.75rem;
        opacity: 0.5;
        margin-top: 0.2rem;
    }

    input[type="date"] {
        width: 100%;
        padding: 0.6rem 0.8rem;
        border: 1px solid var(--border);
        border-radius: 0.6rem;
        background: transparent;
        color: var(--fg);
        font-size: 0.95rem;
    }

    /* --- plan stats --- */
    .stats {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 0.8rem;
        margin-bottom: 0.9rem;
    }

    .stat {
        border: 1px solid var(--border);
        border-radius: 0.9rem;
        padding: 0.7rem 1rem;
        display: flex;
        flex-direction: column;
        gap: 0.2rem;
    }

    .stat-label {
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        opacity: 0.6;
        font-weight: 600;
    }

    .stat-value {
        font-size: 1.7rem;
        font-weight: 800;
        letter-spacing: -0.02em;

        em {
            font-style: normal;
            font-size: 0.9rem;
            font-weight: 600;
            opacity: 0.6;
        }

        &.accent {
            background: linear-gradient(120deg, var(--accent), var(--accent-2));
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        &.range {
            font-size: 1.35rem;
            white-space: nowrap;
        }
    }

    .hours-input {
        width: 3.2rem;
        font-size: 1.7rem;
        font-weight: 800;
        letter-spacing: -0.02em;
        color: var(--fg);
        background: transparent;
        border: none;
        border-bottom: 2px solid var(--accent);
        padding: 0 0.1rem;
        text-align: center;

        &:focus {
            outline: none;
            border-bottom-color: var(--accent-2);
        }
    }

    .verdict {
        font-size: 0.88rem;
        padding: 0.55rem 0.9rem;
        border-radius: 0.7rem;
        border: 1px solid color-mix(in srgb, var(--accent) 45%, var(--border));
        background: color-mix(in srgb, var(--accent) 10%, transparent);
        margin: 0 0 1rem;

        &.warn {
            border-color: #d9a441;
            background: rgba(217, 164, 65, 0.12);
        }
    }

    /* --- commitments --- */
    .quota-row {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.6rem;
        margin-bottom: 0.6rem;
    }

    .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr));
        gap: 0.6rem;
    }

    .card {
        position: relative;
        overflow: visible;
        display: flex;
        flex-direction: column;
        gap: 0.55rem;
        padding: 0.75rem 0.85rem;
        border: 1px solid var(--border);
        border-radius: 0.8rem;
        transition:
            border-color 0.15s ease,
            opacity 0.15s ease;

        &.off {
            opacity: 0.62;
        }
        &:not(.off) {
            border-color: color-mix(in srgb, var(--accent) 40%, var(--border));
        }
    }

    .card-head {
        display: flex;
        align-items: center;
        gap: 0.55rem;
        padding-right: 1.6rem; /* room for the info button */
    }

    .req {
        font-size: 0.64rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        padding: 0.12rem 0.5rem;
        border-radius: 999px;
        color: var(--accent);
        border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
    }

    .info {
        position: absolute;
        top: 0.5rem;
        right: 0.5rem;
        box-sizing: border-box;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex: none;
        padding: 0;
        width: 1.3rem;
        height: 1.3rem;
        aspect-ratio: 1;
        border-radius: 50%;
        border: 1px solid var(--border);
        background: transparent;
        color: var(--fg);
        opacity: 0.55;
        font-size: 0.72rem;
        font-weight: 700;
        font-style: italic;
        font-family: Georgia, "Times New Roman", serif;
        line-height: 1;
        cursor: pointer;
        transition:
            opacity 0.12s ease,
            border-color 0.12s ease,
            color 0.12s ease;

        &:hover,
        &.active {
            opacity: 1;
            border-color: var(--accent);
            color: var(--accent);
        }
    }

    .popover {
        position: absolute;
        top: 2rem;
        right: 0.5rem;
        z-index: 20;
        width: 17rem;
        max-width: calc(100vw - 4rem);
        text-align: start;
        padding: 0.8rem 0.9rem;
        border: 1px solid var(--border);
        border-radius: 0.7rem;
        background: var(--canvas-elevated, var(--canvas));
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
    }

    .switch {
        flex-shrink: 0;
        position: relative;
        width: 2.6rem;
        height: 1.5rem;
        cursor: pointer;

        input {
            opacity: 0;
            width: 100%;
            height: 100%;
            margin: 0;
            cursor: pointer;
        }

        .track {
            position: absolute;
            inset: 0;
            border-radius: 999px;
            background: var(--border);
            transition: background 0.15s ease;
            pointer-events: none;

            &::after {
                content: "";
                position: absolute;
                top: 2px;
                left: 2px;
                width: calc(1.5rem - 4px);
                height: calc(1.5rem - 4px);
                border-radius: 50%;
                background: #fff;
                transition: transform 0.15s ease;
            }
        }

        input:checked + .track {
            background: linear-gradient(120deg, var(--accent), var(--accent-2));
        }
        input:checked + .track::after {
            transform: translateX(1.1rem);
        }
    }

    .name {
        font-weight: 700;
        font-size: 0.92rem;
        line-height: 1.25;
    }

    .chip {
        font-size: 0.72rem;
        font-weight: 700;
        padding: 0.2rem 0.6rem;
        border-radius: 999px;
        white-space: nowrap;
        align-self: flex-start;

        &.on {
            color: var(--accent);
            border: 1px solid color-mix(in srgb, var(--accent) 55%, transparent);
        }
        &.cost {
            color: #c9803a;
            border: 1px solid rgba(201, 128, 58, 0.5);
        }
    }

    .enforce {
        font-size: 0.86rem;
        font-weight: 600;
        margin: 0 0 0.4rem;
    }

    .tradeoff {
        font-size: 0.8rem;
        line-height: 1.5;
        margin: 0;
        opacity: 0.7;

        b {
            color: var(--accent);
            opacity: 1;
        }
    }

    .rates {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 0.4rem;
    }

    .rate {
        display: flex;
        flex-direction: column;
        gap: 0.3rem;
        text-align: start;
        padding: 0.55rem 0.6rem;
        border: 1px solid var(--border);
        border-radius: 0.7rem;
        background: transparent;
        color: var(--fg);
        cursor: pointer;
        transition:
            border-color 0.12s ease,
            background 0.12s ease;

        &:hover {
            border-color: var(--accent);
        }

        &.active {
            border-color: var(--accent);
            background: rgba(69, 160, 90, 0.1);
        }
    }

    .rate-num {
        font-size: 1.15rem;
        font-weight: 800;
        letter-spacing: -0.01em;

        em {
            display: block;
            font-style: normal;
            font-size: 0.68rem;
            font-weight: 600;
            letter-spacing: 0.02em;
            opacity: 0.6;
        }
    }

    .rate-env {
        font-size: 0.72rem;
        line-height: 1.3;
        opacity: 0.75;
    }

    /* --- actions --- */
    .actions {
        margin-top: 1.4rem;
        display: flex;
        justify-content: flex-end;

        &.split {
            justify-content: space-between;
            align-items: center;
            gap: 0.8rem;
            flex-wrap: wrap;
        }
    }

    .cta {
        border: none;
        border-radius: 999px;
        padding: 0.8rem 1.9rem;
        font-size: 1rem;
        font-weight: 700;
        color: #fff;
        cursor: pointer;
        background: linear-gradient(120deg, var(--accent), var(--accent-2));
        box-shadow: 0 5px 16px rgba(69, 160, 90, 0.28);
        transition:
            transform 0.15s ease,
            box-shadow 0.15s ease;

        &:hover {
            transform: translateY(-2px);
            box-shadow: 0 9px 22px rgba(69, 160, 90, 0.4);
        }
        &:active {
            transform: translateY(0);
        }
    }

    .ghost {
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 0.7rem 1.3rem;
        font-size: 0.92rem;
        font-weight: 600;
        background: transparent;
        color: var(--fg);
        cursor: pointer;
        transition: border-color 0.12s ease;

        &:hover {
            border-color: var(--accent);
        }
    }
</style>
