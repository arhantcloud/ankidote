<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    // Ankidote dashboard. Deliberately keeps Memory, Performance and Readiness
    // separate (PRD §4): FSRS-style recall is not the same as exam performance,
    // and neither is a score projection. Each metric carries its own estimate,
    // range, coverage, confidence, timestamp, reasons — and a give-up rule so
    // the app shows nothing when it lacks the data to be honest.
    import { onMount } from "svelte";
    import { goto } from "$app/navigation";
    import type { AnkidoteStats } from "../state";
    import { loadAnkidoteState, loadAnkidoteStats } from "../state";

    interface ScoreRange {
        low: number;
        high: number;
    }

    interface TopicScore {
        topic: string;
        section: string;
        score: ScoreRange;
        questionsAnswered: number;
        questionsCorrect: number;
    }

    interface Diagnostic {
        baseline: number;
        low: number;
        high: number;
        answered: number;
        topicScores: TopicScore[];
        takenAt?: number;
    }

    interface Plan {
        desiredScore: number;
        testDate: string;
    }

    // --- give-up rule (write it down) --------------------------------------
    // Readiness is a projection, so it must refuse to guess without evidence.
    const MIN_REVIEWS = 200;
    const MIN_COVERAGE = 50; // percent of exam topics touched
    const GIVE_UP_RULE = `No projected score until you have at least ${MIN_REVIEWS} graded reviews and ${MIN_COVERAGE}% topic coverage.`;

    // GMAT covers roughly this many named topics across its three sections.
    const TOTAL_EXAM_TOPICS = 15;

    let diagnostic: Diagnostic | null = null;
    let plan: Plan | null = null;
    let stats: AnkidoteStats | null = null;
    let loggedIn = false;

    onMount(async () => {
        // Prefer persisted/synced state from the collection config; fall back
        // to the same-session sessionStorage copies.
        const [state, liveStats] = await Promise.all([
            loadAnkidoteState(),
            loadAnkidoteStats(),
        ]);
        loggedIn = state.loggedIn === true;
        diagnostic =
            (state.diagnostic as Diagnostic | undefined) ??
            readJson<Diagnostic>("ankidote.diagnostic");
        plan = (state.plan as Plan | undefined) ?? readJson<Plan>("ankidote.plan");
        stats = liveStats;
    });

    function readJson<T>(key: string): T | null {
        try {
            const raw = sessionStorage.getItem(key);
            return raw ? (JSON.parse(raw) as T) : null;
        } catch {
            return null;
        }
    }

    function relTime(ts: number | undefined): string {
        if (!ts) {
            return "unknown";
        }
        const mins = Math.round((Date.now() - ts) / 60000);
        if (mins < 1) {
            return "just now";
        }
        if (mins < 60) {
            return `${mins} min ago`;
        }
        const hrs = Math.round(mins / 60);
        if (hrs < 24) {
            return `${hrs} hr${hrs === 1 ? "" : "s"} ago`;
        }
        const days = Math.round(hrs / 24);
        return `${days} day${days === 1 ? "" : "s"} ago`;
    }

    function clampPct(n: number): number {
        return Math.max(0, Math.min(100, Math.round(n)));
    }

    // --- derived measurements (all tied to real data) ----------------------
    $: hasDiag = diagnostic !== null;
    $: topics = diagnostic?.topicScores ?? [];
    $: answered = diagnostic?.answered ?? 0;
    $: coveragePct = clampPct((topics.length / TOTAL_EXAM_TOPICS) * 100);

    // Real graded reviews come from the Anki review log (plus problems done).
    $: practiceHistory = stats?.sessions ?? [];
    $: problemsAnswered = stats?.problemsAnswered ?? 0;
    $: problemsCorrect = stats?.problemsCorrect ?? 0;
    $: gradedReviews = (stats?.gradedReviews ?? 0) + problemsAnswered;

    // Memory: how much of the topic decks is mastered (mature cards) — the same
    // per-topic values that gate problem sets in the loop. Withheld until the
    // topic decks actually hold cards.
    $: memory = stats?.memory ?? null;
    $: topicMastery = stats?.topicMastery ?? [];
    $: memoryHasData =
        memory != null && memory.masteryPct != null && memory.totalCards > 0;
    $: memoryPct = memoryHasData ? clampPct(memory!.masteryPct!) : 0;

    // Performance: chance of getting an exam-style question right — measured
    // from problems actually completed (practice loop + diagnostic items).
    $: diagCorrect = topics.reduce((s, t) => s + t.questionsCorrect, 0);
    $: diagAnswered = topics.reduce((s, t) => s + t.questionsAnswered, 0);
    $: perfCorrect = problemsCorrect + diagCorrect;
    $: perfAnswered = problemsAnswered + diagAnswered;
    $: performanceHasData = perfAnswered > 0;
    $: performancePct = performanceHasData
        ? clampPct((perfCorrect / perfAnswered) * 100)
        : 0;

    // Readiness: the actual current predicted score range from the diagnostic.
    $: desired = plan?.desiredScore ?? 705;
    $: baseline = diagnostic?.baseline ?? 0;
    $: readinessPct =
        hasDiag && desired > 205
            ? clampPct(((baseline - 205) / (desired - 205)) * 100)
            : 0;

    // Confidence in the readiness projection.
    $: readinessReady = gradedReviews >= MIN_REVIEWS && coveragePct >= MIN_COVERAGE;
    function confidenceLevel(coverage: number, reviews: number): string {
        if (coverage >= 80 && reviews >= 500) {
            return "high";
        }
        if (coverage >= 60 && reviews >= 300) {
            return "medium";
        }
        return "low";
    }
    $: confidence = confidenceLevel(coveragePct, gradedReviews);

    $: masteryByTopic = new Map(topicMastery.map((m) => [m.topic, m]));

    $: updated = relTime(diagnostic?.takenAt);

    $: readinessReasons = [
        `Anchored to your diagnostic (${answered} adaptive questions)`,
        `Covers ${coveragePct}% of exam topics so far`,
        `${gradedReviews} graded reviews on record`,
    ];
    $: performanceReasons = [
        `${perfCorrect}/${perfAnswered} exam-style questions correct`,
        `${problemsAnswered} from practice, ${diagAnswered} from diagnostic`,
    ];
    $: memoryReasons = memoryHasData
        ? [
              `${memory!.masteredCards} of ${memory!.totalCards} topic cards mastered`,
              `${memory!.reviews} graded reviews in Anki`,
          ]
        : ["No cards in your topic decks yet"];
</script>

<main class="stats">
    <header class="top">
        <div>
            <span class="eyebrow">Your dashboard</span>
            <h1>Where you stand</h1>
        </div>
        <div class="top-actions">
            {#if hasDiag && loggedIn}
                <button class="cta" on:click={() => goto("/ankidote/loop")}>
                    Start studying &rarr;
                </button>
            {:else if hasDiag}
                <span class="login-note" title="Studying builds synced Anki decks">
                    Log in to AnkiWeb to start studying
                </span>
            {/if}
            <button class="ghost" on:click={() => goto("/ankidote/diagnostic")}>
                Retake diagnostic
            </button>
        </div>
    </header>

    {#if !hasDiag}
        <section class="panel empty">
            <h2>No diagnostic yet</h2>
            <p>
                Take the adaptive diagnostic and your score range, per-topic breakdown,
                and readiness will appear here.
            </p>
            <button class="cta" on:click={() => goto("/ankidote/diagnostic")}>
                Start the diagnostic &rarr;
            </button>
        </section>
    {:else}
        <!-- Current score range (the latest direct measurement) -->
        <section class="panel hero">
            <div class="hero-main">
                <span class="stat-label">Current score range</span>
                <div class="big-range">{diagnostic?.low}&ndash;{diagnostic?.high}</div>
                <p class="hero-sub">
                    Latest diagnostic: <b>{baseline}</b>
                    midpoint · {answered} questions · updated {updated}
                </p>
            </div>
            {#if plan}
                <div class="hero-goal">
                    <span class="stat-label">Target</span>
                    <div class="goal-score">{desired}</div>
                    <span class="goal-sub">exam {plan.testDate}</span>
                </div>
            {/if}
        </section>

        <!-- Memory / Performance / Readiness, shown separately -->
        <section class="beakers">
            {#each [{ key: "memory", label: "Memory", tint: "blue", pct: memoryPct, hasData: memoryHasData }, { key: "performance", label: "Performance", tint: "amber", pct: performancePct, hasData: performanceHasData }, { key: "readiness", label: "Readiness", tint: "grass", pct: readinessPct, hasData: readinessReady }] as b (b.key)}
                <div class="beaker-card {b.tint}">
                    <div class="beaker-head">
                        <span class="beaker-title">{b.label}</span>
                    </div>

                    <div class="beaker-wrap">
                        <svg viewBox="0 0 80 104" class="beaker" aria-hidden="true">
                            <defs>
                                <clipPath id="clip-{b.key}">
                                    <path
                                        d="M26 8 h28 v30 l16 46 a8 8 0 0 1 -7.4 11 H17.4
                                           a8 8 0 0 1 -7.4 -11 l16 -46 z"
                                    />
                                </clipPath>
                            </defs>
                            <g clip-path="url(#clip-{b.key})">
                                <rect
                                    class="liquid-bg"
                                    x="0"
                                    y="0"
                                    width="80"
                                    height="104"
                                />
                                {#if b.hasData}
                                    <g
                                        class="liquid-g"
                                        style="transform: scaleY({b.pct / 100});"
                                    >
                                        <rect
                                            class="liquid"
                                            x="0"
                                            y="0"
                                            width="80"
                                            height="104"
                                        />
                                    </g>
                                {/if}
                            </g>
                            <path
                                class="glass"
                                d="M26 8 h28 v30 l16 46 a8 8 0 0 1 -7.4 11 H17.4
                                   a8 8 0 0 1 -7.4 -11 l16 -46 z"
                            />
                            <line class="lip" x1="24" y1="8" x2="56" y2="8" />
                        </svg>

                        <div class="beaker-read">
                            {#if !b.hasData}
                                <div class="no-score">—</div>
                            {:else if b.key === "readiness"}
                                <div class="pct">{baseline}</div>
                                <div class="pct-sub">projected</div>
                            {:else}
                                <div class="pct">
                                    {b.pct}
                                    <em>%</em>
                                </div>
                            {/if}
                        </div>
                    </div>

                    {#if b.key === "readiness"}
                        {#if readinessReady}
                            <p class="metric-line">
                                Projected <b>{baseline}</b>
                                · likely
                                <b>{diagnostic?.low}&ndash;{diagnostic?.high}</b>
                            </p>
                            <p class="metric-meta">
                                Confidence: <b class="conf {confidence}">
                                    {confidence}
                                </b>
                                · {coveragePct}% covered · {updated}
                            </p>
                            <ul class="reasons">
                                {#each readinessReasons as r}
                                    <li>{r}</li>
                                {/each}
                            </ul>
                        {:else}
                            <p class="metric-line withheld">Not enough data yet.</p>
                            <p class="metric-meta">
                                {coveragePct}% covered · {gradedReviews} reviews
                            </p>
                            <p class="rule">{GIVE_UP_RULE}</p>
                        {/if}
                    {:else if b.key === "memory"}
                        <p class="metric-line">
                            How much of your topic decks is mastered.
                        </p>
                        {#if memoryHasData}
                            <p class="metric-meta">Mature cards across topic decks</p>
                        {:else}
                            <p class="metric-meta withheld">
                                Sort &amp; study cards to build mastery.
                            </p>
                        {/if}
                        <ul class="reasons">
                            {#each memoryReasons as r}
                                <li>{r}</li>
                            {/each}
                        </ul>
                    {:else}
                        <p class="metric-line">
                            Chance you answer an exam-style question right.
                        </p>
                        {#if performanceHasData}
                            <p class="metric-meta">
                                From {perfAnswered} problems completed · {updated}
                            </p>
                        {:else}
                            <p class="metric-meta withheld">
                                Answer practice problems to measure this.
                            </p>
                        {/if}
                        <ul class="reasons">
                            {#each performanceReasons as r}
                                <li>{r}</li>
                            {/each}
                        </ul>
                    {/if}
                </div>
            {/each}
        </section>

        <div class="cols">
            <!-- Per-topic score ranges -->
            <section class="panel">
                <h2>By topic</h2>
                <div class="topic-table">
                    {#each topics as t (t.topic)}
                        <div class="topic-row">
                            <div class="topic-info">
                                <span class="topic-name">{t.topic}</span>
                                <span class="topic-sec">{t.section}</span>
                            </div>
                            <span class="topic-mastery">
                                {#if masteryByTopic.has(t.topic)}
                                    {masteryByTopic.get(t.topic)
                                        ?.mastered}/{masteryByTopic.get(t.topic)?.total} mastered
                                {:else}
                                    —
                                {/if}
                            </span>
                            <span class="topic-range">
                                {t.score?.low}&ndash;{t.score?.high}
                            </span>
                        </div>
                    {/each}
                </div>
            </section>

            <!-- Practice history (real problem sessions) -->
            <section class="panel">
                <h2>Practice history</h2>
                {#if practiceHistory.length}
                    <div class="history">
                        {#each practiceHistory as s}
                            <div class="hist-row">
                                <span class="hist-when">
                                    {s.daysAgo === 0 ? "Today" : `${s.daysAgo}d ago`}
                                </span>
                                <span class="hist-kind prob" title={s.topic}>
                                    {s.topic || "Problems"}
                                </span>
                                <span class="hist-count">{s.count}</span>
                                <div class="hist-bar">
                                    <div
                                        class="hist-fill"
                                        style="width: {s.accuracy}%"
                                    ></div>
                                </div>
                                <span class="hist-acc">{s.accuracy}%</span>
                            </div>
                        {/each}
                    </div>
                {:else}
                    <p class="empty-note">
                        No practice problems yet. Start a study loop and your completed
                        problem sets will show up here.
                    </p>
                {/if}
            </section>
        </div>
    {/if}
</main>

<style lang="scss">
    .stats {
        --accent: #45a05a;
        --accent-2: #2e7d46;
        --blue: #4a86c5;
        --amber: #d99a3c;
        min-height: 100vh;
        max-width: 62rem;
        margin: 0 auto;
        padding: 2.5rem 1.5rem 4rem;
        color: var(--fg);
    }

    .top {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        margin-bottom: 1.4rem;
        gap: 1rem;
    }

    .top-actions {
        display: flex;
        gap: 0.6rem;
        align-items: center;
        flex-wrap: wrap;
    }

    .eyebrow {
        display: inline-block;
        font-size: 0.76rem;
        font-weight: 700;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--accent);
        margin-bottom: 0.3rem;
    }

    h1 {
        font-size: clamp(1.6rem, 4vw, 2.2rem);
        font-weight: 800;
        letter-spacing: -0.02em;
        margin: 0;
    }

    h2 {
        font-size: 1rem;
        font-weight: 700;
        margin: 0 0 0.9rem;
    }

    .panel {
        border: 1px solid var(--border);
        border-radius: 1.1rem;
        background: var(--canvas-elevated, var(--canvas));
        padding: 1.3rem 1.4rem;
        margin-bottom: 1.1rem;
    }

    .empty {
        text-align: center;
        padding: 3rem 1.5rem;

        p {
            opacity: 0.75;
            max-width: 32rem;
            margin: 0.5rem auto 1.4rem;
            line-height: 1.6;
        }
    }

    /* --- hero score range --- */
    .hero {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 1.5rem;
        flex-wrap: wrap;
    }

    .stat-label {
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        opacity: 0.6;
        font-weight: 600;
    }

    .big-range {
        font-size: clamp(2.4rem, 7vw, 3.6rem);
        font-weight: 800;
        letter-spacing: -0.03em;
        line-height: 1.05;
        background: linear-gradient(120deg, var(--accent), var(--accent-2));
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 0.2rem 0 0.5rem;
    }

    .hero-sub {
        font-size: 0.9rem;
        opacity: 0.8;
        margin: 0;

        b {
            color: var(--fg);
        }
    }

    .hero-goal {
        text-align: center;
    }

    .goal-score {
        font-size: 2rem;
        font-weight: 800;
        letter-spacing: -0.02em;
    }

    .goal-sub {
        font-size: 0.78rem;
        opacity: 0.6;
    }

    /* --- beakers --- */
    .beakers {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 1.1rem;
        margin-bottom: 1.1rem;
    }

    .beaker-card {
        border: 1px solid var(--border);
        border-radius: 1.1rem;
        background: var(--canvas-elevated, var(--canvas));
        padding: 1.2rem 1.2rem 1.3rem;

        &.blue {
            --tint: var(--blue);
        }
        &.amber {
            --tint: var(--amber);
        }
        &.grass {
            --tint: var(--accent);
        }
    }

    .beaker-head {
        display: flex;
        justify-content: center;
        margin-bottom: 0.4rem;
    }

    .beaker-title {
        font-size: 0.95rem;
        font-weight: 700;
    }

    .beaker-wrap {
        position: relative;
        display: flex;
        justify-content: center;
        margin: 0.2rem 0 0.9rem;
    }

    .beaker {
        width: 96px;
        height: 124px;
    }

    .liquid-bg {
        fill: color-mix(in srgb, var(--tint) 12%, transparent);
    }

    .liquid-g {
        transform-box: fill-box;
        transform-origin: bottom;
        transition: transform 0.7s cubic-bezier(0.22, 1, 0.36, 1);
    }

    .liquid {
        fill: var(--tint);
        opacity: 0.85;
    }

    .glass {
        fill: none;
        stroke: color-mix(in srgb, var(--tint) 55%, var(--border));
        stroke-width: 2.5;
    }

    .lip {
        stroke: color-mix(in srgb, var(--tint) 55%, var(--border));
        stroke-width: 3;
        stroke-linecap: round;
    }

    .beaker-read {
        position: absolute;
        inset: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        pointer-events: none;
    }

    .pct {
        font-size: 1.7rem;
        font-weight: 800;
        letter-spacing: -0.02em;

        em {
            font-style: normal;
            font-size: 0.9rem;
            opacity: 0.7;
        }
    }

    .pct-sub {
        font-size: 0.66rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        opacity: 0.65;
    }

    .no-score {
        font-size: 2rem;
        font-weight: 800;
        opacity: 0.45;
    }

    .metric-line {
        font-size: 0.85rem;
        line-height: 1.4;
        margin: 0 0 0.35rem;
        text-align: center;

        &.withheld {
            font-weight: 700;
        }
    }

    .metric-meta {
        font-size: 0.74rem;
        opacity: 0.75;
        text-align: center;
        margin: 0 0 0.5rem;

        &.withheld {
            font-weight: 600;
            opacity: 0.9;
        }
    }

    .empty-note {
        font-size: 0.85rem;
        line-height: 1.5;
        opacity: 0.65;
        margin: 0;
    }

    .conf {
        text-transform: capitalize;

        &.low {
            color: #d9a441;
        }
        &.medium {
            color: var(--blue);
        }
        &.high {
            color: var(--accent);
        }
    }

    .reasons {
        list-style: none;
        margin: 0;
        padding: 0;

        li {
            font-size: 0.73rem;
            opacity: 0.7;
            padding: 0.15rem 0 0.15rem 0.9rem;
            position: relative;

            &::before {
                content: "·";
                position: absolute;
                left: 0.2rem;
                color: var(--tint);
                font-weight: 800;
            }
        }
    }

    .rule {
        font-size: 0.72rem;
        line-height: 1.4;
        opacity: 0.7;
        border-top: 1px dashed var(--border);
        padding-top: 0.5rem;
        margin: 0.3rem 0 0;
        font-style: italic;
    }

    /* --- two columns --- */
    .cols {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 1.1rem;
    }

    .topic-table {
        display: flex;
        flex-direction: column;
    }

    .topic-row {
        display: grid;
        grid-template-columns: 1fr auto auto;
        gap: 0.9rem;
        align-items: center;
        padding: 0.55rem 0;

        &:not(:last-child) {
            border-bottom: 1px solid var(--border);
        }
    }

    .topic-info {
        display: flex;
        flex-direction: column;
    }

    .topic-name {
        font-weight: 600;
        font-size: 0.9rem;
    }

    .topic-sec {
        font-size: 0.72rem;
        opacity: 0.55;
    }

    .topic-mastery {
        font-size: 0.82rem;
        opacity: 0.7;
        white-space: nowrap;
    }

    .topic-range {
        font-weight: 700;
        font-size: 0.9rem;
        color: var(--accent);
    }

    /* --- practice history --- */
    .history {
        display: flex;
        flex-direction: column;
        gap: 0.6rem;
    }

    .hist-row {
        display: grid;
        grid-template-columns: 3.4rem 7rem 2rem 1fr 2.6rem;
        gap: 0.6rem;
        align-items: center;
        font-size: 0.82rem;
    }

    .hist-when {
        opacity: 0.6;
    }

    .hist-kind {
        font-weight: 600;
        font-size: 0.72rem;
        text-align: center;
        padding: 0.15rem 0.3rem;
        border-radius: 999px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;

        &.card {
            color: var(--accent);
            background: rgba(69, 160, 90, 0.12);
        }
        &.prob {
            color: var(--amber);
            background: rgba(217, 154, 60, 0.14);
        }
    }

    .hist-count {
        opacity: 0.7;
        text-align: end;
    }

    .hist-bar {
        height: 7px;
        border-radius: 999px;
        background: var(--border);
        overflow: hidden;
    }

    .hist-fill {
        height: 100%;
        border-radius: 999px;
        background: linear-gradient(90deg, var(--accent-2), var(--accent));
    }

    .hist-acc {
        font-weight: 700;
        text-align: end;
        opacity: 0.85;
    }

    /* --- buttons --- */
    .cta {
        border: none;
        border-radius: 999px;
        padding: 0.7rem 1.6rem;
        font-size: 0.98rem;
        font-weight: 700;
        color: #fff;
        cursor: pointer;
        background: linear-gradient(120deg, var(--accent), var(--accent-2));
        box-shadow: 0 5px 16px rgba(69, 160, 90, 0.28);
    }

    .ghost {
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 0.55rem 1.1rem;
        font-size: 0.88rem;
        font-weight: 600;
        color: var(--fg);
        background: transparent;
        cursor: pointer;
        transition: border-color 0.15s ease;
        white-space: nowrap;

        &:hover {
            border-color: var(--accent);
        }
    }

    .login-note {
        font-size: 0.82rem;
        font-weight: 600;
        opacity: 0.75;
        padding: 0.55rem 0.9rem;
        border: 1px dashed var(--border);
        border-radius: 999px;
        white-space: nowrap;
    }

    @media (max-width: 40rem) {
        .beakers {
            grid-template-columns: 1fr;
        }
        .cols {
            grid-template-columns: 1fr;
        }
    }
</style>
