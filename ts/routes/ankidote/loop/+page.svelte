<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    // The study loop (PRD §5). One weak topic at a time: study its Anki cards
    // (native reviewer), then hand off to the isolated practice-problem page
    // (/ankidote/problems) to re-estimate the topic. This page owns topic
    // selection, the cards step, cadence prompts (check-ins / organize) and the
    // weekly-problem vial; the problem set itself lives on its own page.
    import { onMount } from "svelte";
    import { goto } from "$app/navigation";
    import { bridgeCommand, bridgeCommandsAvailable } from "@tslib/bridgecommand";
    import { Shell, Card, Badge, Button, PlanVial } from "../_lib";
    import type { AnkidotePlanVial } from "../state";

    interface ScoreRange {
        low: number;
        high: number;
    }

    interface Question {
        id: string;
        topic: string;
        stem: string;
        choices: string[];
    }

    interface LoopState {
        phase:
            | "empty"
            | "login_required"
            | "cards"
            | "organize"
            | "problems_offer"
            | "problems"
            | "reveal"
            | "update"
            | "day_done";
        topic?: string;
        section?: string;
        sectionLabel?: string;
        deck?: string;
        weightPct?: number;
        target?: number;
        hasDiagnostic?: boolean;
        topicScore?: ScoreRange | null;
        overall?: ScoreRange | null;
        masteryGained?: number;
        mastered?: number;
        total?: number;
        problemsUnlocked?: boolean;
        problemsDoneForDay?: boolean;
        problemsRemaining?: number;
        question?: Question | null;
        commitments?: Record<string, boolean>;
        planVial?: AnkidotePlanVial;
        cardsVial?: AnkidotePlanVial;
        checkinDue?: boolean;
        checkinBlocking?: boolean;
    }

    let state: LoopState = { phase: "empty" };
    let loading = false;
    let errorMessage = "";
    let gateMessage = "";

    // Problem-solving and the mandatory concept lesson each live on their own
    // isolated page; those phases mean we belong there, not here.
    const PROBLEM_PHASES = ["problems", "reveal", "update"];

    async function post(endpoint: string, body: unknown = {}): Promise<LoopState> {
        const resp = await fetch(`/_anki/${endpoint}`, {
            method: "POST",
            headers: { "Content-Type": "application/binary" },
            body: JSON.stringify(body ?? {}),
        });
        if (!resp.ok) {
            throw new Error(await resp.text());
        }
        return JSON.parse(await resp.text()) as LoopState;
    }

    function apply(next: LoopState): void {
        state = next;
        if (next.phase === "organize") {
            goto(
                next.topic
                    ? `/ankidote/organize?topic=${encodeURIComponent(next.topic)}`
                    : "/ankidote/organize",
            );
        } else if (PROBLEM_PHASES.includes(next.phase)) {
            goto("/ankidote/problems");
        }
    }

    async function refresh(): Promise<void> {
        loading = true;
        errorMessage = "";
        try {
            apply(await post("ankidoteLoopState"));
        } catch (err) {
            errorMessage = `${err}`;
        } finally {
            loading = false;
        }
    }

    onMount(refresh);

    function studyCards(): void {
        if (!state.topic) {
            return;
        }
        if (bridgeCommandsAvailable()) {
            bridgeCommand(`ankidote:study:${state.topic}`);
        } else {
            errorMessage = "Open this inside Anki to study the deck.";
        }
    }

    function goProblems(topic?: string): void {
        goto(
            topic
                ? `/ankidote/problems?topic=${encodeURIComponent(topic)}`
                : "/ankidote/problems",
        );
    }

    async function run(endpoint: string, body: unknown = {}): Promise<void> {
        loading = true;
        errorMessage = "";
        try {
            apply(await post(endpoint, body));
        } catch (err) {
            errorMessage = `${err}`;
        } finally {
            loading = false;
        }
    }

    const skipProblems = (topic?: string) =>
        run("ankidoteLoopSkip", topic ? { topic } : {});
    const anotherTopic = (topic?: string) =>
        run("ankidoteLoopAnother", topic ? { topic } : {});

    function onProblemsClick(): void {
        if (!state.problemsUnlocked) {
            gateMessage = problemsHint(state);
            return;
        }
        gateMessage = "";
        goProblems(state.topic);
    }

    function problemsHint(s: LoopState): string {
        const n = s.problemsRemaining ?? 0;
        return `Study more cards first. ${n} more card${
            n === 1 ? "" : "s"
        } need to reach a 3 day interval before problems unlock.`;
    }

    // --- check-ins (stale-targeted mini-CAT) -------------------------------
    interface CheckinState {
        finished: boolean;
        answered: number;
        maxQuestions: number;
        score?: ScoreRange;
        question?: { id: string; stem: string; choices: string[] } | null;
        before?: Record<string, ScoreRange>;
        after?: Record<string, ScoreRange>;
    }
    let checkin: CheckinState | null = null;
    let checkinChoice: number | null = null;

    async function startCheckin(): Promise<void> {
        loading = true;
        errorMessage = "";
        try {
            checkin = (await post("ankidoteCheckinStart")) as unknown as CheckinState;
            checkinChoice = null;
        } catch (err) {
            errorMessage = `${err}`;
        } finally {
            loading = false;
        }
    }

    async function answerCheckin(): Promise<void> {
        if (!checkin?.question || checkinChoice === null) {
            return;
        }
        loading = true;
        try {
            checkin = (await post("ankidoteCheckinAnswer", {
                itemId: checkin.question.id,
                chosenChoice: checkinChoice,
            })) as unknown as CheckinState;
            checkinChoice = null;
        } catch (err) {
            errorMessage = `${err}`;
        } finally {
            loading = false;
        }
    }

    async function finishCheckin(): Promise<void> {
        checkin = null;
        await refresh();
    }

    $: topicRange = state.topicScore
        ? `${state.topicScore.low}–${state.topicScore.high}`
        : "not yet measured";
</script>

<Shell align="top" max="46rem">
    <header class="top">
        <div>
            <Badge variant="green" dot>Study loop</Badge>
            <h1>{state.topic ?? ""}</h1>
        </div>
    </header>

    {#if state.planVial && state.phase !== "login_required" && state.phase !== "empty"}
        <PlanVial vial={state.planVial} />
    {/if}

    <!-- Cadence prompts (gated). -->
    {#if state.checkinDue && !checkin && state.phase !== "login_required"}
        <Card>
            <div class="banner" class:blocking={state.checkinBlocking}>
                <div>
                    <h3>
                        {state.checkinBlocking ? "Check in required" : "Check in due"}
                    </h3>
                    <p>
                        A quick ~10 question check in reanchors your score range on the
                        topics that have gone stale.
                        {#if state.checkinBlocking}
                            Your estimate can't be left stale any longer.
                        {/if}
                    </p>
                </div>
                <div class="banner-actions">
                    <Button on:click={startCheckin} disabled={loading}>
                        Start check in
                    </Button>
                </div>
            </div>
        </Card>
    {/if}

    {#if checkin}
        <Card>
            {#if !checkin.finished && checkin.question}
                <div class="step">
                    <span class="step-num">✓</span>
                    <div class="step-body">
                        <h3>
                            Check in · {checkin.answered + 1}/{checkin.maxQuestions}
                        </h3>
                        <p class="stem">{checkin.question.stem}</p>
                        <div class="choices">
                            {#each checkin.question.choices as choice, index}
                                <button
                                    class="choice"
                                    class:selected={checkinChoice === index}
                                    on:click={() => (checkinChoice = index)}
                                >
                                    <span class="letter">
                                        {String.fromCharCode(65 + index)}
                                    </span>
                                    <span>{choice}</span>
                                </button>
                            {/each}
                        </div>
                        <div class="actions end">
                            <Button
                                on:click={answerCheckin}
                                disabled={checkinChoice === null || loading}
                            >
                                {loading ? "…" : "Submit"}
                            </Button>
                        </div>
                    </div>
                </div>
            {:else}
                <div class="step-body">
                    <h3>Check in complete</h3>
                    <p>Your score range has been reanchored.</p>
                    {#if checkin.before && checkin.after}
                        <div class="beforeafter">
                            {#each Object.keys(checkin.after) as topic}
                                <div class="ba-row">
                                    <span class="ba-topic">{topic}</span>
                                    <span class="ba-before">
                                        {checkin.before[topic]?.low ?? "–"}–{checkin
                                            .before[topic]?.high ?? "–"}
                                    </span>
                                    <span class="ba-arrow">→</span>
                                    <span class="ba-after">
                                        {checkin.after[topic].low}–{checkin.after[topic]
                                            .high}
                                    </span>
                                </div>
                            {/each}
                        </div>
                    {/if}
                    <div class="actions end">
                        <Button on:click={finishCheckin}>Back to the loop →</Button>
                    </div>
                </div>
            {/if}
        </Card>
    {:else if state.phase === "login_required"}
        <Card>
            <div class="empty">
                <h2>Log in to start studying</h2>
                <p>
                    Studying builds real Anki decks that sync to your account. Log in to
                    AnkiWeb (Sync) to unlock the study loop; your diagnostic and plan
                    will be restored from your account.
                </p>
                <Button on:click={() => goto("/ankidote/stats")}>
                    Back to dashboard &rarr;
                </Button>
            </div>
        </Card>
    {:else if state.phase === "empty"}
        <Card>
            <div class="empty">
                <h2>No diagnostic yet</h2>
                <p>Take the diagnostic first so the loop knows where you're weakest.</p>
                <Button on:click={() => goto("/ankidote/diagnostic")}>
                    Start the diagnostic &rarr;
                </Button>
            </div>
        </Card>
    {:else if state.phase === "day_done"}
        <Card>
            <div class="empty">
                <h2>You're done for today 🎉</h2>
                <p>
                    Every topic deck is finished for the day. Come back tomorrow when
                    new cards are due, or review your dashboard.
                </p>
                {#if state.overall}
                    <p class="hero-sub">
                        Current range: <b>
                            {state.overall.low}&ndash;{state.overall.high}
                        </b>
                    </p>
                {/if}
                <Button on:click={() => goto("/ankidote/stats")}>
                    View my dashboard &rarr;
                </Button>
            </div>
        </Card>
    {:else if !state.checkinBlocking}
        <Card corners>
            <div class="topic-line">
                <Badge variant="green">{state.sectionLabel}</Badge>
                <h2>{state.topic}</h2>
                <span class="weight">{state.weightPct}% of your total score</span>
            </div>
            <div class="ranges">
                <div class="range">
                    <span class="range-label">This topic</span>
                    <span class="range-value">{topicRange}</span>
                </div>
                {#if state.overall}
                    <div class="range">
                        <span class="range-label">Overall</span>
                        <span class="range-value">
                            {state.overall.low}–{state.overall.high}
                        </span>
                    </div>
                {/if}
                <div class="range">
                    <span class="range-label">Target</span>
                    <span class="range-value">{state.target}</span>
                </div>
            </div>
        </Card>

        {#if state.phase === "cards"}
            <Card>
                <div class="loop-buttons">
                    <Button size="sm" on:click={studyCards}>
                        Study cards
                    </Button>
                    <Button
                        size="sm"
                        variant="outline"
                        on:click={() => anotherTopic(state.topic)}
                        disabled={loading}
                    >
                        Change topic
                    </Button>
                    {#if state.problemsUnlocked}
                        <Button size="sm" on:click={onProblemsClick} disabled={loading}>
                            Do problems
                        </Button>
                    {:else}
                        <button
                            class="locked"
                            title={problemsHint(state)}
                            on:click={onProblemsClick}
                            disabled={loading}
                        >
                            Do problems
                        </button>
                    {/if}
                </div>
                {#if gateMessage && !state.problemsUnlocked}
                    <p class="note gate-note">{gateMessage}</p>
                {/if}
            </Card>
        {:else if state.phase === "problems_offer"}
            <Card>
                <div class="step">
                    <span class="step-num">2</span>
                    <div class="step-body">
                        <h3>Deck done for today. Test what stuck?</h3>
                        <p>
                            <b>{state.masteryGained}</b>
                            more card{state.masteryGained === 1 ? "" : "s"} in
                            <b>{state.topic}</b>
                            {state.masteryGained === 1 ? "has" : "have"} moved past a 3 day
                            interval since your last check ({state.mastered}/{state.total}
                            mastered). That's enough change to remeasure with a few practice
                            problems.
                        </p>
                        <div class="actions">
                            <Button on:click={() => goProblems(state.topic)}>
                                Do problems
                            </Button>
                            <Button
                                variant="outline"
                                on:click={() => skipProblems(state.topic)}
                            >
                                Not yet → next topic
                            </Button>
                        </div>
                    </div>
                </div>
            </Card>
        {/if}
    {/if}

    {#if errorMessage}
        <p class="error">{errorMessage}</p>
    {/if}
</Shell>

<style lang="scss">
    @use "../_lib/theme" as ad;

    .top {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        margin-bottom: 1.6rem;
        gap: 1rem;
    }

    .note {
        font-family: ad.$font-mono;
        font-size: 0.8rem;
        color: ad.$green;
        margin: -0.4rem 0 1rem;
    }

    h1 {
        font-family: ad.$font-heading;
        font-size: clamp(1.5rem, 4vw, 2rem);
        font-weight: 700;
        letter-spacing: -0.02em;
        margin: 0.5rem 0 0;
    }

    h2 {
        font-family: ad.$font-heading;
        font-size: 1.3rem;
        font-weight: 600;
        margin: 0;
    }

    h3 {
        font-family: ad.$font-heading;
        font-size: 1.05rem;
        font-weight: 600;
        margin: 0 0 0.4rem;
    }

    :global(.ad-content > .ad-card) {
        margin-bottom: 1.1rem;
    }

    .banner {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;

        p {
            color: ad.$muted;
            margin: 0.2rem 0 0;
            font-size: 0.9rem;
        }

        &.blocking h3 {
            color: ad.$danger;
        }
    }

    .banner-actions {
        flex: none;
    }

    .empty {
        text-align: center;
        padding: 1.5rem 0.5rem;

        p {
            color: ad.$muted;
            line-height: 1.6;
            margin: 0.6rem 0 1.4rem;
        }

        b {
            color: ad.$green;
        }
    }

    .topic-line {
        display: flex;
        align-items: center;
        gap: 0.7rem;
        flex-wrap: wrap;
        margin-bottom: 1rem;
    }

    .weight {
        font-family: ad.$font-mono;
        font-size: 0.78rem;
        color: ad.$muted;
    }

    .ranges {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 0.7rem;
        margin-bottom: 0.9rem;
    }

    .range {
        border: 1px solid ad.$border;
        border-radius: ad.$r-input;
        padding: 0.6rem 0.8rem;
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
        background: rgba(255, 255, 255, 0.02);
    }

    .range-label {
        font-family: ad.$font-mono;
        font-size: 0.66rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: ad.$muted;
    }

    .range-value {
        font-family: ad.$font-mono;
        font-weight: 500;
        font-size: 1.05rem;
        color: ad.$fg;
    }

    .loop-buttons {
        display: flex;
        flex-wrap: nowrap;
        justify-content: center;
        gap: 0.5rem;
    }

    // Equal-width buttons that share the row; text wraps inside if tight.
    .loop-buttons > :global(button) {
        flex: 1 1 0;
        min-width: 0;
        white-space: normal;
    }

    .step {
        display: flex;
        gap: 1rem;
        align-items: flex-start;
    }

    .step-num {
        flex: none;
        width: 2.2rem;
        height: 2.2rem;
        border-radius: 50%;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-family: ad.$font-heading;
        font-weight: 700;
        color: #fff;
        background: linear-gradient(to right, ad.$serum, ad.$green);
        box-shadow: 0 0 18px -4px rgba(34, 197, 94, 0.6);
    }

    .step-body {
        flex: 1;
        min-width: 0;

        p {
            color: ad.$muted;
            line-height: 1.6;
            margin: 0 0 0.9rem;

            b {
                color: ad.$fg;
                font-weight: 600;
            }
        }
    }

    code {
        font-family: ad.$font-mono;
        font-size: 0.85em;
        padding: 0.1rem 0.4rem;
        border-radius: 0.35rem;
        background: ad.$green-wash;
        color: ad.$green;
    }

    .stem {
        font-size: 1.05rem;
        line-height: 1.6;
        white-space: pre-line;
        color: ad.$fg !important;
    }

    .choices {
        display: flex;
        flex-direction: column;
        gap: 0.55rem;
        margin-bottom: 1rem;
    }

    .choice {
        display: flex;
        align-items: center;
        gap: 0.8rem;
        width: 100%;
        text-align: start;
        font-family: ad.$font-body;
        font-size: 0.96rem;
        padding: 0.75rem 0.9rem;
        border: 1px solid ad.$border;
        border-radius: ad.$r-input;
        background: rgba(0, 0, 0, 0.25);
        color: ad.$fg;
        cursor: pointer;
        transition:
            border-color 0.15s ease,
            background 0.15s ease,
            box-shadow 0.15s ease;

        &:hover {
            border-color: ad.$border-hi;
        }

        &.selected {
            border-color: ad.$green;
            background: ad.$green-wash;
            box-shadow: 0 0 20px -8px rgba(34, 197, 94, 0.5);

            .letter {
                background: linear-gradient(to right, ad.$serum, ad.$green);
                color: #fff;
                border-color: transparent;
            }
        }
    }

    .letter {
        flex: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.7rem;
        height: 1.7rem;
        border-radius: 50%;
        border: 1px solid ad.$border;
        font-family: ad.$font-mono;
        font-size: 0.78rem;
        font-weight: 500;
    }

    .beforeafter {
        display: flex;
        flex-direction: column;
        gap: 0.4rem;
        margin: 0.6rem 0 1rem;
        font-family: ad.$font-mono;
        font-size: 0.85rem;

        .ba-row {
            display: flex;
            align-items: center;
            gap: 0.6rem;
        }

        .ba-topic {
            min-width: 7rem;
            color: ad.$fg;
        }

        .ba-before {
            color: ad.$muted;
        }

        .ba-after {
            color: ad.$green;
            font-weight: 600;
        }

        .ba-arrow {
            color: ad.$muted;
        }
    }

    .actions {
        display: flex;
        gap: 0.7rem;
        flex-wrap: wrap;

        &.end {
            justify-content: flex-end;
        }
    }

    .locked {
        border: 1px solid ad.$border;
        border-radius: ad.$r-pill;
        min-height: 44px;
        padding: 0.5rem 1.1rem;
        font-family: ad.$font-body;
        font-size: 0.85rem;
        font-weight: 600;
        color: ad.$muted;
        background: rgba(255, 255, 255, 0.04);
        cursor: help;

        &:hover {
            border-color: ad.$border;
        }
    }

    .gate-note {
        color: ad.$muted;
        margin-top: 0.6rem;
    }

    .error {
        color: ad.$danger;
        font-size: 0.9rem;
    }

    @media (max-width: 34rem) {
        .ranges {
            grid-template-columns: 1fr;
        }
    }
</style>
