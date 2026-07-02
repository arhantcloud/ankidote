<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    // The basic study loop (PRD §5, minimal): pick the lowest-scoring topic ->
    // study its Anki cards (native reviewer) -> answer 2-3 practice problems ->
    // re-estimate theta -> show the updated score -> next topic. Extra loop
    // features (mistake review, subsumption, ranking, quizzes) come later.
    import { onMount } from "svelte";
    import { goto } from "$app/navigation";
    import { bridgeCommand, bridgeCommandsAvailable } from "@tslib/bridgecommand";

    interface ScoreRange {
        low: number;
        high: number;
    }

    interface Question {
        id: string;
        section: number;
        topic: string;
        subtopic: string;
        stem: string;
        choices: string[];
    }

    interface LoopState {
        phase:
            | "empty"
            | "login_required"
            | "cards"
            | "problems_offer"
            | "problems"
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
        result?: {
            score: ScoreRange;
            questionsAnswered: number;
            questionsCorrect: number;
        } | null;
        question?: Question | null;
    }

    let state: LoopState = { phase: "empty" };
    let question: Question | undefined;
    let selectedChoice: number | null = null;
    let loading = false;
    let sorting = false;
    let errorMessage = "";
    let sortNote = "";
    let gateMessage = "";

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
        question = next.question ?? undefined;
        selectedChoice = null;
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

    async function startProblems(topic?: string): Promise<void> {
        loading = true;
        errorMessage = "";
        try {
            apply(await post("ankidoteLoopStart", topic ? { topic } : {}));
        } catch (err) {
            errorMessage = `${err}`;
        } finally {
            loading = false;
        }
    }

    async function skipProblems(topic?: string): Promise<void> {
        loading = true;
        errorMessage = "";
        try {
            apply(await post("ankidoteLoopSkip", topic ? { topic } : {}));
        } catch (err) {
            errorMessage = `${err}`;
        } finally {
            loading = false;
        }
    }

    async function sortDecks(): Promise<void> {
        sorting = true;
        errorMessage = "";
        sortNote = "";
        try {
            const res = (await post("ankidoteSortDecks")) as unknown as {
                total: number;
            };
            sortNote = `Sorted ${res.total} cards into topic decks.`;
            await refresh();
        } catch (err) {
            errorMessage = `${err}`;
        } finally {
            sorting = false;
        }
    }

    async function submitAnswer(): Promise<void> {
        if (selectedChoice === null || !question) {
            return;
        }
        loading = true;
        errorMessage = "";
        try {
            apply(
                await post("ankidoteLoopAnswer", {
                    problemId: question.id,
                    chosenChoice: selectedChoice,
                }),
            );
        } catch (err) {
            errorMessage = `${err}`;
        } finally {
            loading = false;
        }
    }

    async function nextTopic(): Promise<void> {
        loading = true;
        errorMessage = "";
        try {
            apply(await post("ankidoteLoopNext"));
        } catch (err) {
            errorMessage = `${err}`;
        } finally {
            loading = false;
        }
    }

    async function anotherTopic(topic?: string): Promise<void> {
        loading = true;
        errorMessage = "";
        try {
            apply(await post("ankidoteLoopAnother", topic ? { topic } : {}));
        } catch (err) {
            errorMessage = `${err}`;
        } finally {
            loading = false;
        }
    }

    function onProblemsClick(): void {
        if (!state.problemsUnlocked) {
            gateMessage = problemsHint(state);
            return;
        }
        gateMessage = "";
        startProblems(state.topic);
    }

    function problemsHint(s: LoopState): string {
        if (!s.problemsDoneForDay) {
            return "Study more cards first — finish today's cards for this deck, then keep maturing them.";
        }
        const n = s.problemsRemaining ?? 0;
        return `Study more cards first — ${n} more card${
            n === 1 ? "" : "s"
        } need to reach a 3-day interval before problems unlock.`;
    }

    $: topicRange = state.topicScore
        ? `${state.topicScore.low}–${state.topicScore.high}`
        : "not yet measured";
</script>

<main class="loop">
    <header class="top">
        <div>
            <span class="eyebrow">Study loop</span>
            <h1>One weak topic at a time</h1>
        </div>
        <div class="top-actions">
            <button class="ghost" on:click={sortDecks} disabled={sorting}>
                {sorting ? "Sorting…" : "Sort decks by topic"}
            </button>
            <button class="ghost" on:click={() => goto("/ankidote/stats")}>
                Dashboard
            </button>
        </div>
    </header>

    {#if sortNote}
        <p class="note">{sortNote}</p>
    {/if}

    {#if state.phase === "login_required"}
        <section class="panel empty">
            <h2>Log in to start studying</h2>
            <p>
                Studying builds real Anki decks that sync to your account. Log in to
                AnkiWeb (Sync) to unlock the study loop; your diagnostic and plan will
                be restored from your account.
            </p>
            <button class="cta" on:click={() => goto("/ankidote/stats")}>
                Back to dashboard &rarr;
            </button>
        </section>
    {:else if state.phase === "empty"}
        <section class="panel empty">
            <h2>No diagnostic yet</h2>
            <p>Take the diagnostic first so the loop knows where you're weakest.</p>
            <button class="cta" on:click={() => goto("/ankidote/diagnostic")}>
                Start the diagnostic &rarr;
            </button>
        </section>
    {:else if state.phase === "day_done"}
        <section class="panel empty">
            <h2>You're done for today 🎉</h2>
            <p>
                Every topic deck is finished for the day. Come back tomorrow when new
                cards are due, or review your dashboard.
            </p>
            {#if state.overall}
                <p class="hero-sub">
                    Current range: <b>{state.overall.low}&ndash;{state.overall.high}</b>
                </p>
            {/if}
            <button class="cta" on:click={() => goto("/ankidote/stats")}>
                View my dashboard &rarr;
            </button>
        </section>
    {:else}
        <section class="panel topic-head">
            <div class="topic-line">
                <span class="tag section">{state.sectionLabel}</span>
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
            <p class="why">
                Picked because it sits furthest below your target once weighted by how
                much it's worth on the GMAT.
            </p>
        </section>

        {#if state.phase === "cards"}
            <section class="panel step">
                <span class="step-num">1</span>
                <div class="step-body">
                    <h3>Study flashcards</h3>
                    <p>
                        Cards come from your Anki deck
                        <code>{state.deck}</code>
                        . Study them with the normal reviewer (spaced repetition). When the
                        deck is done for the day, you'll head back here.
                    </p>
                    <div class="actions">
                        <button class="cta" on:click={studyCards}>
                            Study cards in this deck
                        </button>
                        <button
                            class="cta alt"
                            on:click={() => anotherTopic(state.topic)}
                            disabled={loading}
                        >
                            Study a different topic
                        </button>
                    </div>
                    <div class="actions">
                        <button
                            class="cta"
                            class:locked={!state.problemsUnlocked}
                            title={state.problemsUnlocked
                                ? "Test what you've learned with practice problems"
                                : problemsHint(state)}
                            on:click={onProblemsClick}
                            disabled={loading}
                        >
                            Do problems for this topic
                        </button>
                    </div>
                    {#if gateMessage && !state.problemsUnlocked}
                        <p class="note gate-note">{gateMessage}</p>
                    {/if}
                </div>
            </section>
        {:else if state.phase === "problems_offer"}
            <section class="panel step">
                <span class="step-num">2</span>
                <div class="step-body">
                    <h3>Deck done for today — test what stuck?</h3>
                    <p>
                        <b>{state.masteryGained}</b>
                        more card{state.masteryGained === 1 ? "" : "s"} in
                        <b>{state.topic}</b>
                        {state.masteryGained === 1 ? "has" : "have"} moved past a 3-day interval
                        since your last check ({state.mastered}/{state.total} mastered). That's
                        enough change to re-measure with a few practice problems.
                    </p>
                    <div class="actions">
                        <button class="cta" on:click={() => startProblems(state.topic)}>
                            Do problems for this topic
                        </button>
                        <button
                            class="cta alt"
                            on:click={() => skipProblems(state.topic)}
                        >
                            Not yet → next topic
                        </button>
                    </div>
                </div>
            </section>
        {:else if state.phase === "problems" && question}
            <section class="panel step">
                <span class="step-num">2</span>
                <div class="step-body">
                    <h3>Practice problems</h3>
                    <p class="stem">{question.stem}</p>
                    <div class="choices">
                        {#each question.choices as choice, index}
                            <button
                                class="choice"
                                class:selected={selectedChoice === index}
                                on:click={() => (selectedChoice = index)}
                            >
                                <span class="letter">
                                    {String.fromCharCode(65 + index)}
                                </span>
                                <span>{choice}</span>
                            </button>
                        {/each}
                    </div>
                    <div class="actions end">
                        <button
                            class="cta"
                            on:click={submitAnswer}
                            disabled={selectedChoice === null || loading}
                        >
                            {loading ? "Scoring…" : "Submit answer"}
                        </button>
                    </div>
                </div>
            </section>
        {:else if state.phase === "update" && state.result}
            <section class="panel step done">
                <span class="step-num">✓</span>
                <div class="step-body">
                    <h3>Score updated</h3>
                    <p>
                        {state.result.questionsCorrect}/{state.result.questionsAnswered} correct.
                        New range for
                        <b>{state.topic}</b>
                        :
                        <b>{state.result.score.low}–{state.result.score.high}</b>
                        .
                    </p>
                    <div class="actions end">
                        <button class="cta" on:click={nextTopic}>Next topic →</button>
                    </div>
                </div>
            </section>
        {/if}
    {/if}

    {#if errorMessage}
        <p class="error">{errorMessage}</p>
    {/if}
</main>

<style lang="scss">
    .loop {
        --accent: #45a05a;
        --accent-2: #2e7d46;
        min-height: 100vh;
        max-width: 46rem;
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
        gap: 0.5rem;
        align-items: center;
        flex-wrap: wrap;
    }

    .note {
        font-size: 0.85rem;
        color: var(--accent);
        margin: -0.6rem 0 1rem;
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
        font-size: clamp(1.5rem, 4vw, 2rem);
        font-weight: 800;
        letter-spacing: -0.02em;
        margin: 0;
    }

    h2 {
        font-size: 1.3rem;
        font-weight: 800;
        margin: 0;
    }

    h3 {
        font-size: 1.05rem;
        font-weight: 700;
        margin: 0 0 0.4rem;
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
            margin: 0.5rem 0 1.4rem;
        }
    }

    .topic-line {
        display: flex;
        align-items: center;
        gap: 0.7rem;
        flex-wrap: wrap;
        margin-bottom: 0.9rem;
    }

    .weight {
        font-size: 0.82rem;
        opacity: 0.65;
    }

    .tag {
        font-size: 0.74rem;
        font-weight: 700;
        padding: 0.2rem 0.6rem;
        border-radius: 999px;
        border: 1px solid var(--accent);
        color: var(--accent);
    }

    .ranges {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 0.7rem;
        margin-bottom: 0.7rem;
    }

    .range {
        border: 1px solid var(--border);
        border-radius: 0.7rem;
        padding: 0.5rem 0.7rem;
        display: flex;
        flex-direction: column;
        gap: 0.15rem;
    }

    .range-label {
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.07em;
        opacity: 0.6;
        font-weight: 600;
    }

    .range-value {
        font-weight: 800;
        font-size: 1.05rem;
    }

    .why {
        font-size: 0.82rem;
        opacity: 0.7;
        margin: 0;
    }

    .step {
        display: flex;
        gap: 1rem;
        align-items: flex-start;
    }

    .step-num {
        flex: none;
        width: 2rem;
        height: 2rem;
        border-radius: 50%;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-weight: 800;
        color: #fff;
        background: linear-gradient(120deg, var(--accent), var(--accent-2));
    }

    .step-body {
        flex: 1;
        min-width: 0;

        p {
            opacity: 0.85;
            line-height: 1.5;
            margin: 0 0 0.9rem;
        }
    }

    code {
        font-size: 0.85em;
        padding: 0.1rem 0.35rem;
        border-radius: 0.35rem;
        background: rgba(69, 160, 90, 0.12);
        color: var(--accent);
    }

    .stem {
        font-size: 1.05rem;
        line-height: 1.6;
        white-space: pre-line;
    }

    .choices {
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
        margin-bottom: 1rem;
    }

    .choice {
        display: flex;
        align-items: center;
        gap: 0.8rem;
        width: 100%;
        text-align: start;
        font-size: 0.96rem;
        padding: 0.7rem 0.85rem;
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

        &.selected {
            border-color: var(--accent);
            background: rgba(69, 160, 90, 0.1);

            .letter {
                background: var(--accent);
                color: #fff;
                border-color: var(--accent);
            }
        }
    }

    .letter {
        flex: none;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.6rem;
        height: 1.6rem;
        border-radius: 50%;
        border: 1px solid var(--border);
        font-size: 0.78rem;
        font-weight: 700;
    }

    .actions {
        display: flex;
        gap: 0.7rem;
        flex-wrap: wrap;

        &.end {
            justify-content: flex-end;
        }
    }

    .actions + .actions {
        margin-top: 0.7rem;
    }

    .cta {
        border: none;
        border-radius: 999px;
        padding: 0.7rem 1.5rem;
        font-size: 0.95rem;
        font-weight: 700;
        color: #fff;
        cursor: pointer;
        background: linear-gradient(120deg, var(--accent), var(--accent-2));
        box-shadow: 0 5px 16px rgba(69, 160, 90, 0.28);
        transition: transform 0.15s ease;

        &:hover:not(:disabled) {
            transform: translateY(-2px);
        }
        &:disabled {
            background: #8a8f98;
            color: #fff;
            box-shadow: none;
            cursor: default;
        }
        &.locked {
            background: #8a8f98;
            color: #fff;
            box-shadow: none;
            cursor: help;

            &:hover {
                transform: none;
            }
        }
        &.alt {
            color: var(--fg);
            background: transparent;
            border: 1px solid var(--border);
            box-shadow: none;
        }
    }

    .gate-note {
        color: #8a8f98;
        margin-top: 0.5rem;
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

        &:hover {
            border-color: var(--accent);
        }
    }

    .error {
        color: #e05b5b;
        font-size: 0.9rem;
    }

    @media (max-width: 34rem) {
        .ranges {
            grid-template-columns: 1fr;
        }
    }
</style>
