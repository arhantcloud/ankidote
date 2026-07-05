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
    import { loadAnkidoteAuth, loadAnkidoteState, loadAnkidoteStats } from "../state";
    import { Shell, Card, Badge, Button, Beaker } from "../_lib";

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

    // Memory/Performance/Readiness gate: only surface these once every exam topic
    // has been covered (cards mastered / problems completed) AND a meaningful
    // volume of work is finished, so the numbers aren't drawn from a thin sample.
    const MIN_MEMORY_CARDS = 100;
    const MIN_PERFORMANCE_PROBLEMS = 100;

    const GIVE_UP_RULE = `No projected score until every topic has ${MIN_MEMORY_CARDS}+ cards mastered and ${MIN_PERFORMANCE_PROBLEMS}+ problems completed, with at least ${MIN_REVIEWS} graded reviews and ${MIN_COVERAGE}% topic coverage.`;

    // GMAT covers roughly this many named topics across its three sections.
    const TOTAL_EXAM_TOPICS = 15;

    let diagnostic: Diagnostic | null = null;
    let plan: Plan | null = null;
    let stats: AnkidoteStats | null = null;
    let loggedIn = false;

    onMount(async () => {
        // Stats are per-account; require sign-in and read only the authoritative
        // synced state (collection config, pulled from Firebase on login). We do
        // NOT fall back to sessionStorage here — that cache is not scoped to an
        // account, so it would leak a previous user's diagnostic into a fresh or
        // signed-out account.
        const auth = await loadAnkidoteAuth();
        if (!auth.loggedIn) {
            goto("/ankidote/login");
            return;
        }
        const [state, liveStats] = await Promise.all([
            loadAnkidoteState(),
            loadAnkidoteStats(),
        ]);
        loggedIn = state.loggedIn === true;
        diagnostic = (state.diagnostic as Diagnostic | undefined) ?? null;
        plan = (state.plan as Plan | undefined) ?? null;
        stats = liveStats;
    });

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
    // per-topic values that gate problem sets in the loop. Withheld until every
    // topic has mastered cards and at least MIN_MEMORY_CARDS cards are mastered.
    $: memory = stats?.memory ?? null;
    $: topicMastery = stats?.topicMastery ?? [];
    $: cardsCompleted = memory?.masteredCards ?? 0;
    $: allTopicsHaveCards =
        topics.length > 0 &&
        topics.every((t) => (masteryByTopic.get(t.topic)?.mastered ?? 0) > 0);
    $: memoryHasData =
        memory != null &&
        memory.masteryPct != null &&
        memory.totalCards > 0 &&
        allTopicsHaveCards &&
        cardsCompleted >= MIN_MEMORY_CARDS;
    $: memoryPct = memoryHasData ? clampPct(memory!.masteryPct!) : 0;

    // Performance: chance of getting an exam-style question right — measured
    // from problems actually completed (practice loop + diagnostic items).
    // Withheld until every topic has problems completed and at least
    // MIN_PERFORMANCE_PROBLEMS problems are finished.
    $: diagCorrect = topics.reduce((s, t) => s + t.questionsCorrect, 0);
    $: diagAnswered = topics.reduce((s, t) => s + t.questionsAnswered, 0);
    $: perfCorrect = problemsCorrect + diagCorrect;
    $: perfAnswered = problemsAnswered + diagAnswered;
    $: allTopicsHaveProblems =
        topics.length > 0 &&
        topics.every(
            (t) =>
                (t.questionsAnswered ?? 0) > 0 ||
                practiceHistory.some((s) => s.topic === t.topic && s.count > 0),
        );
    $: performanceHasData =
        perfAnswered >= MIN_PERFORMANCE_PROBLEMS && allTopicsHaveProblems;
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

    // Confidence in the readiness projection. Held back until every topic has
    // its cards and problems completed (same gate as Memory + Performance), on
    // top of the reviews/coverage floor.
    $: readinessReady =
        gradedReviews >= MIN_REVIEWS &&
        coveragePct >= MIN_COVERAGE &&
        memoryHasData &&
        performanceHasData;
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
    $: performanceReasons = performanceHasData
        ? [
              `${perfCorrect}/${perfAnswered} exam-style questions correct`,
              `${problemsAnswered} from practice, ${diagAnswered} from diagnostic`,
          ]
        : [
              `${perfAnswered}/${MIN_PERFORMANCE_PROBLEMS} problems completed`,
              allTopicsHaveProblems
                  ? "All topics have problems"
                  : "Some topics still need problems",
          ];
    $: memoryReasons = memoryHasData
        ? [
              `${memory!.masteredCards} of ${memory!.totalCards} topic cards mastered`,
              `${memory!.reviews} graded reviews in Anki`,
          ]
        : [
              `${cardsCompleted}/${MIN_MEMORY_CARDS} cards mastered`,
              allTopicsHaveCards
                  ? "All topics have cards"
                  : "Some topics still need cards",
          ];
</script>

<Shell align="top" max="64rem">
    <div class="stack">
        <header class="top">
            <div>
                <Badge variant="green" dot>Your dashboard</Badge>
                <h1>Where you stand</h1>
            </div>
            <div class="top-actions">
                {#if hasDiag && loggedIn}
                    <Button size="sm" on:click={() => goto("/ankidote/loop")}>
                        Start studying &rarr;
                    </Button>
                {:else if hasDiag}
                    <span class="login-note" title="Studying builds synced Anki decks">
                        Log in to AnkiWeb to start studying
                    </span>
                {/if}
                {#if plan}
                    <Button
                        variant="outline"
                        size="sm"
                        on:click={() => goto("/ankidote/brew")}
                    >
                        My Brew
                    </Button>
                {/if}
                <Button
                    variant="outline"
                    size="sm"
                    on:click={() => goto("/ankidote/diagnostic")}
                >
                    Retake diagnostic
                </Button>
            </div>
        </header>

        {#if !hasDiag}
            <Card>
                <div class="empty">
                    <h2>No diagnostic yet</h2>
                    <p>
                        Take the adaptive diagnostic and your score range, per topic
                        breakdown, and readiness will appear here.
                    </p>
                    <Button on:click={() => goto("/ankidote/diagnostic")}>
                        Start the diagnostic &rarr;
                    </Button>
                </div>
            </Card>
        {:else}
            <!-- Current score range (the latest direct measurement) -->
            <Card>
                <div class="hero">
                    <div class="hero-main">
                        <span class="stat-label">Current score range</span>
                        <div class="big-range">
                            {diagnostic?.low}&ndash;{diagnostic?.high}
                        </div>
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
                </div>
            </Card>

            <!-- Memory / Performance / Readiness, shown separately -->
            <section class="beakers">
                {#each [{ key: "memory", label: "Memory", tint: "lime", color: "var(--lime)", shape: "round" as const, seed: 7314, pct: memoryPct, hasData: memoryHasData }, { key: "performance", label: "Performance", tint: "amber", color: "var(--amber)", shape: "vial" as const, seed: 5261, pct: performancePct, hasData: performanceHasData }, { key: "readiness", label: "Readiness", tint: "grass", color: "var(--green)", shape: "round" as const, seed: 7314, pct: readinessPct, hasData: readinessReady }] as b (b.key)}
                    <div class="beaker-card {b.tint}">
                        <div class="beaker-head">
                            <span class="beaker-title">{b.label}</span>
                        </div>

                        <div class="beaker-slot">
                            <Beaker
                                tint={b.color}
                                seed={b.seed}
                                shape={b.shape}
                                fill={b.hasData ? b.pct : null}
                                width={104}
                                height={134}
                            />
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
                                <p class="metric-meta">
                                    Mature cards across topic decks
                                </p>
                            {:else}
                                <p class="metric-meta withheld">
                                    Master {MIN_MEMORY_CARDS}+ cards across every topic
                                    to unlock.
                                </p>
                            {/if}
                            <ul class="reasons">
                                {#each memoryReasons as r}
                                    <li>{r}</li>
                                {/each}
                            </ul>
                        {:else}
                            <p class="metric-line">
                                Chance you answer an exam style question right.
                            </p>
                            {#if performanceHasData}
                                <p class="metric-meta">
                                    From {perfAnswered} problems completed · {updated}
                                </p>
                            {:else}
                                <p class="metric-meta withheld">
                                    Finish {MIN_PERFORMANCE_PROBLEMS}+ problems across
                                    every topic to unlock.
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
                <Card>
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
                                            ?.mastered}/{masteryByTopic.get(t.topic)
                                            ?.total} mastered
                                    {:else}
                                        -
                                    {/if}
                                </span>
                                <span class="topic-range">
                                    {t.score?.low}&ndash;{t.score?.high}
                                </span>
                            </div>
                        {/each}
                    </div>
                </Card>

                <!-- Practice history (real problem sessions) -->
                <Card>
                    <h2>Practice history</h2>
                    {#if practiceHistory.length}
                        <div class="history">
                            {#each practiceHistory as s}
                                <div class="hist-row">
                                    <span class="hist-when">
                                        {s.daysAgo === 0
                                            ? "Today"
                                            : `${s.daysAgo}d ago`}
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
                            No practice problems yet. Start a study loop and your
                            completed problem sets will show up here.
                        </p>
                    {/if}
                </Card>
            </div>
        {/if}
    </div>
</Shell>

<style lang="scss">
    @use "../_lib/theme" as ad;

    .stack {
        display: flex;
        flex-direction: column;
        gap: 1.1rem;

        // Palette for the three-beaker instrument panel. Amber is the one warm
        // signal we allow, to distinguish "performance" at a glance.
        --green: #{ad.$green};
        --lime: #{ad.$lime};
        --amber: #e0a758;
    }

    .top {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 1rem;
        flex-wrap: wrap;
    }

    .top-actions {
        display: flex;
        gap: 0.5rem;
        align-items: center;
        flex-wrap: wrap;
    }

    h1 {
        font-family: ad.$font-heading;
        font-size: clamp(1.6rem, 4vw, 2.2rem);
        font-weight: 700;
        letter-spacing: -0.02em;
        margin: 0.5rem 0 0;
    }

    h2 {
        font-family: ad.$font-heading;
        font-size: 1rem;
        font-weight: 600;
        margin: 0 0 0.9rem;
    }

    .empty {
        text-align: center;
        padding: 2rem 1rem;

        p {
            color: ad.$muted;
            max-width: 32rem;
            margin: 0.6rem auto 1.4rem;
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
        font-family: ad.$font-mono;
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: ad.$muted;
    }

    .big-range {
        font-family: ad.$font-heading;
        font-size: clamp(2.6rem, 7vw, 3.8rem);
        font-weight: 700;
        letter-spacing: -0.03em;
        line-height: 1.05;
        @include ad.gradient-text(ad.$green, ad.$lime);
        margin: 0.3rem 0 0.5rem;
    }

    .hero-sub {
        font-size: 0.9rem;
        color: ad.$muted;
        margin: 0;

        b {
            color: ad.$fg;
        }
    }

    .hero-goal {
        text-align: center;
    }

    .goal-score {
        font-family: ad.$font-heading;
        font-size: 2rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: ad.$fg;
    }

    .goal-sub {
        font-family: ad.$font-mono;
        font-size: 0.72rem;
        color: ad.$muted;
    }

    /* --- beakers --- */
    .beakers {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 1.1rem;
    }

    .beaker-card {
        border: 1px solid ad.$border;
        border-radius: ad.$r-card;
        background: ad.$surface;
        padding: 1.3rem 1.2rem;
        transition:
            border-color 0.3s ease,
            box-shadow 0.3s ease;

        &.lime {
            --tint: var(--lime);
        }
        &.amber {
            --tint: var(--amber);
        }
        &.grass {
            --tint: var(--green);
        }

        &:hover {
            border-color: color-mix(in srgb, var(--tint) 50%, transparent);
            box-shadow: 0 0 30px -12px color-mix(in srgb, var(--tint) 60%, transparent);
        }
    }

    .beaker-head {
        display: flex;
        justify-content: center;
        margin-bottom: 0.4rem;
    }

    .beaker-title {
        font-family: ad.$font-heading;
        font-size: 0.95rem;
        font-weight: 600;
    }

    .beaker-slot {
        display: flex;
        justify-content: center;
        margin: 0.2rem 0 0.9rem;
    }

    .metric-line {
        font-size: 0.85rem;
        line-height: 1.4;
        margin: 0 0 0.35rem;
        text-align: center;
        color: ad.$fg;

        b {
            color: ad.$green;
        }

        &.withheld {
            font-weight: 600;
            color: ad.$muted;
        }
    }

    .metric-meta {
        font-family: ad.$font-mono;
        font-size: 0.7rem;
        color: ad.$muted;
        text-align: center;
        margin: 0 0 0.5rem;

        &.withheld {
            color: ad.$muted;
        }
    }

    .empty-note {
        font-size: 0.85rem;
        line-height: 1.5;
        color: ad.$muted;
        margin: 0;
    }

    .conf {
        text-transform: capitalize;

        &.low {
            color: var(--amber);
        }
        &.medium {
            color: var(--lime);
        }
        &.high {
            color: var(--green);
        }
    }

    .reasons {
        list-style: none;
        margin: 0;
        padding: 0;

        li {
            font-family: ad.$font-mono;
            font-size: 0.7rem;
            color: ad.$muted;
            padding: 0.18rem 0 0.18rem 0.9rem;
            position: relative;

            &::before {
                content: "›";
                position: absolute;
                left: 0.2rem;
                color: var(--tint);
                font-weight: 700;
            }
        }
    }

    .rule {
        font-size: 0.72rem;
        line-height: 1.4;
        color: ad.$muted;
        border-top: 1px dashed ad.$border;
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
        padding: 0.6rem 0;

        &:not(:last-child) {
            border-bottom: 1px solid ad.$border;
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
        font-family: ad.$font-mono;
        font-size: 0.68rem;
        color: ad.$muted;
    }

    .topic-mastery {
        font-family: ad.$font-mono;
        font-size: 0.78rem;
        color: ad.$muted;
        white-space: nowrap;
    }

    .topic-range {
        font-family: ad.$font-mono;
        font-weight: 500;
        font-size: 0.9rem;
        color: ad.$green;
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
        font-family: ad.$font-mono;
        font-size: 0.78rem;
    }

    .hist-when {
        color: ad.$muted;
    }

    .hist-kind {
        font-size: 0.68rem;
        text-align: center;
        padding: 0.15rem 0.3rem;
        border-radius: ad.$r-pill;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;

        &.prob {
            color: var(--amber);
            background: rgba(224, 167, 88, 0.14);
        }
    }

    .hist-count {
        color: ad.$muted;
        text-align: end;
    }

    .hist-bar {
        height: 7px;
        border-radius: ad.$r-pill;
        background: rgba(255, 255, 255, 0.08);
        overflow: hidden;
    }

    .hist-fill {
        height: 100%;
        border-radius: ad.$r-pill;
        background: linear-gradient(to right, ad.$serum, ad.$green);
    }

    .hist-acc {
        font-weight: 500;
        text-align: end;
        color: ad.$fg;
    }

    .login-note {
        font-family: ad.$font-mono;
        font-size: 0.72rem;
        color: ad.$muted;
        padding: 0.55rem 0.9rem;
        border: 1px dashed ad.$border;
        border-radius: ad.$r-pill;
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
