<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    // Talks to the Python CAT/IRT engine over the mediasrv JSON endpoints
    // (AntiPlan/diagnostic-cat-plan.md §4.4). The auth header is injected for
    // this trusted webview by AuthInterceptor, and application/binary is
    // required by the API's CSRF guard even for a JSON body.
    import { goto } from "$app/navigation";
    import { saveAnkidoteState } from "../state";

    interface ScoreRange {
        low: number;
        high: number;
    }

    interface DiagnosticQuestion {
        id: string;
        section: number;
        topic: string;
        subtopic: string;
        stem: string;
        choices: string[];
    }

    interface TopicScore {
        topic: string;
        section: string;
        theta: number;
        standardError: number;
        score: ScoreRange;
        questionsAnswered: number;
        questionsCorrect: number;
    }

    interface DiagnosticState {
        finished: boolean;
        answered: number;
        maxQuestions: number;
        theta: number;
        standardError: number;
        score: ScoreRange;
        question: DiagnosticQuestion | null;
        topicScores: TopicScore[];
    }

    type Phase = "intro" | "question" | "done";

    let phase: Phase = "intro";
    let loading = false;
    let errorMessage = "";

    let question: DiagnosticQuestion | undefined;
    let selectedChoice: number | null = null;
    let latest: DiagnosticState | undefined;

    async function post(endpoint: string, body: unknown): Promise<DiagnosticState> {
        const resp = await fetch(`/_anki/${endpoint}`, {
            method: "POST",
            headers: { "Content-Type": "application/binary" },
            body: JSON.stringify(body ?? {}),
        });
        if (!resp.ok) {
            throw new Error(await resp.text());
        }
        return JSON.parse(await resp.text()) as DiagnosticState;
    }

    function apply(state: DiagnosticState): void {
        latest = state;
        if (state.finished) {
            phase = "done";
            question = undefined;
        } else {
            phase = "question";
            question = state.question ?? undefined;
            selectedChoice = null;
        }
    }

    async function begin(): Promise<void> {
        loading = true;
        errorMessage = "";
        try {
            apply(await post("ankidoteDiagStart", {}));
        } catch (err) {
            errorMessage = `Something went wrong: ${err}`;
        } finally {
            loading = false;
        }
    }

    async function confirmAnswer(): Promise<void> {
        if (selectedChoice === null || !question) {
            return;
        }
        loading = true;
        errorMessage = "";
        try {
            apply(
                await post("ankidoteDiagAnswer", {
                    itemId: question.id,
                    chosenChoice: selectedChoice,
                }),
            );
        } catch (err) {
            errorMessage = `Something went wrong: ${err}`;
        } finally {
            loading = false;
        }
    }

    const sectionNames: Record<number, string> = {
        1: "Quant",
        2: "Verbal",
        3: "Data Insights",
    };

    // Progress against the (adaptive) item budget.
    $: progress = latest
        ? Math.min(Math.max(latest.answered / latest.maxQuestions, 0), 1)
        : 0;
    $: scoreLabel = latest?.score ? `${latest.score.low}–${latest.score.high}` : "";
    // Midpoint of the range is the point-estimate baseline the goal screen
    // uses to anchor its time-to-target methodology.
    $: baseline = latest?.score
        ? Math.round((latest.score.low + latest.score.high) / 2)
        : 0;

    async function goToGoal(): Promise<void> {
        // Hand the measured baseline to the goal/plan screen, and persist it to
        // the collection config so it saves and syncs (aqt/mediasrv.py).
        if (latest?.score) {
            const diagnostic = {
                low: latest.score.low,
                high: latest.score.high,
                baseline,
                answered: latest.answered,
                topicScores: latest.topicScores,
                takenAt: Date.now(),
            };
            try {
                sessionStorage.setItem(
                    "ankidote.diagnostic",
                    JSON.stringify(diagnostic),
                );
            } catch {
                // sessionStorage may be unavailable; goal screen falls back.
            }
            await saveAnkidoteState({ diagnostic });
        }
        goto("/ankidote/goal");
    }
</script>

<main class="diagnostic">
    {#if phase === "intro"}
        <section class="panel intro">
            <span class="eyebrow">Ankidote diagnostic</span>
            <h1>Find out where you stand.</h1>
            <p class="sub">
                An adaptive test: each question is picked to teach us the most about
                your ability, so it stays short. You'll get a GMAT score range &mdash;
                overall and per topic &mdash; not a false-precision point score.
            </p>
            <ul class="facts">
                <li>
                    <b>Adaptive length</b>
                    &mdash; it stops as soon as your score range is tight enough
                </li>
                <li>
                    <b>Quant, Verbal, Data Insights</b>
                    &mdash; all three sections
                </li>
                <li>
                    <b>No going back</b>
                    &mdash; just like test day
                </li>
            </ul>
            <button class="cta" on:click={begin} disabled={loading}>
                {loading ? "Preparing…" : "Start the diagnostic"}
            </button>
            {#if errorMessage}
                <p class="error">{errorMessage}</p>
            {/if}
        </section>
    {:else if phase === "question" && question && latest}
        <section class="panel">
            <header class="status">
                <div class="progress-track">
                    <div class="progress-fill" style="width: {progress * 100}%"></div>
                </div>
                <div class="status-row">
                    <span class="counter">
                        Question {latest.answered + 1} &middot; adaptive
                    </span>
                    {#if latest.answered > 0}
                        <span class="score-chip">
                            current range: <b>{scoreLabel}</b>
                        </span>
                    {/if}
                </div>
            </header>

            <div class="tags">
                <span class="tag section">{sectionNames[question.section] ?? ""}</span>
                <span class="tag">{question.topic}</span>
                <span class="tag subtle">{question.subtopic}</span>
            </div>

            <p class="stem">{question.stem}</p>

            <div class="choices">
                {#each question.choices as choice, index}
                    <button
                        class="choice"
                        class:selected={selectedChoice === index}
                        on:click={() => (selectedChoice = index)}
                    >
                        <span class="letter">{String.fromCharCode(65 + index)}</span>
                        <span>{choice}</span>
                    </button>
                {/each}
            </div>

            <div class="actions">
                <button
                    class="cta"
                    on:click={confirmAnswer}
                    disabled={selectedChoice === null || loading}
                >
                    {loading ? "Scoring…" : "Confirm answer"}
                </button>
            </div>
            {#if errorMessage}
                <p class="error">{errorMessage}</p>
            {/if}
        </section>
    {:else if phase === "done" && latest}
        <section class="panel results">
            <span class="eyebrow">Diagnostic complete</span>
            <h1>Your current score range</h1>
            <div class="big-score">{scoreLabel}</div>
            <p class="sub">
                Based on {latest.answered} adaptive questions. The band narrows as Ankidote
                gathers more evidence &mdash; certainty is earned, not assumed.
            </p>

            <h2>By topic</h2>
            <div class="topic-table">
                {#each latest.topicScores as topic}
                    <div class="topic-row">
                        <span class="topic-name">{topic.topic}</span>
                        <span class="topic-correct">
                            {topic.questionsCorrect}/{topic.questionsAnswered} correct
                        </span>
                        <span class="topic-range">
                            {topic.score?.low}&ndash;{topic.score?.high}
                        </span>
                    </div>
                {/each}
            </div>

            <div class="actions split">
                <button class="ghost" on:click={begin} disabled={loading}>
                    Retake diagnostic
                </button>
                <button class="cta" on:click={goToGoal} disabled={loading}>
                    Set your goal &rarr;
                </button>
            </div>
        </section>
    {/if}
</main>

<style lang="scss">
    .diagnostic {
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
        max-width: 44rem;
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

    h2 {
        font-size: 1.05rem;
        font-weight: 700;
        margin: 2rem 0 0.8rem;
    }

    .sub {
        font-size: 1rem;
        line-height: 1.6;
        opacity: 0.8;
        margin: 0 0 1.4rem;
    }

    .facts {
        list-style: none;
        padding: 0;
        margin: 0 0 1.8rem;

        li {
            padding: 0.45rem 0;
            border-bottom: 1px solid var(--border);
            font-size: 0.95rem;
            opacity: 0.9;

            &:last-child {
                border-bottom: none;
            }

            b {
                color: var(--accent);
            }
        }
    }

    .status {
        margin-bottom: 1.4rem;
    }

    .progress-track {
        height: 6px;
        border-radius: 999px;
        background: var(--border);
        overflow: hidden;
        margin-bottom: 0.6rem;
    }

    .progress-fill {
        height: 100%;
        border-radius: 999px;
        background: linear-gradient(90deg, var(--accent), var(--accent-2));
        transition: width 0.3s ease;
    }

    .status-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.85rem;
        opacity: 0.85;
    }

    .score-chip {
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 0.2rem 0.7rem;

        b {
            color: var(--accent);
        }
    }

    .tags {
        display: flex;
        gap: 0.4rem;
        flex-wrap: wrap;
        margin-bottom: 1rem;
    }

    .tag {
        font-size: 0.78rem;
        font-weight: 600;
        padding: 0.25rem 0.7rem;
        border-radius: 999px;
        border: 1px solid var(--border);

        &.section {
            color: var(--accent);
            border-color: var(--accent);
        }

        &.subtle {
            opacity: 0.65;
        }
    }

    .stem {
        font-size: 1.08rem;
        line-height: 1.6;
        white-space: pre-line;
        margin: 0 0 1.4rem;
    }

    .choices {
        display: flex;
        flex-direction: column;
        gap: 0.55rem;
    }

    .choice {
        display: flex;
        align-items: center;
        gap: 0.8rem;
        width: 100%;
        text-align: start;
        font-size: 0.98rem;
        line-height: 1.45;
        padding: 0.75rem 0.9rem;
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
        flex-shrink: 0;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.7rem;
        height: 1.7rem;
        border-radius: 50%;
        border: 1px solid var(--border);
        font-size: 0.8rem;
        font-weight: 700;
    }

    .actions {
        margin-top: 1.6rem;
        display: flex;
        justify-content: flex-end;
    }

    .actions.split {
        justify-content: space-between;
        align-items: center;
    }

    .ghost {
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 0.7rem 1.4rem;
        font-size: 0.95rem;
        font-weight: 600;
        color: var(--fg);
        background: transparent;
        cursor: pointer;
        transition: border-color 0.15s ease;

        &:hover:not(:disabled) {
            border-color: var(--accent);
        }

        &:disabled {
            opacity: 0.55;
            cursor: default;
        }
    }

    .cta {
        border: none;
        border-radius: 999px;
        padding: 0.75rem 1.8rem;
        font-size: 1rem;
        font-weight: 700;
        color: #fff;
        cursor: pointer;
        background: linear-gradient(120deg, var(--accent), var(--accent-2));
        box-shadow: 0 5px 16px rgba(69, 160, 90, 0.28);
        transition:
            transform 0.15s ease,
            opacity 0.15s ease;

        &:hover:not(:disabled) {
            transform: translateY(-2px);
        }

        &:disabled {
            opacity: 0.55;
            cursor: default;
        }
    }

    .error {
        margin-top: 1rem;
        color: #e05b5b;
        font-size: 0.9rem;
    }

    .results {
        text-align: center;

        .big-score {
            font-size: clamp(2.6rem, 8vw, 4rem);
            font-weight: 800;
            letter-spacing: -0.03em;
            background: linear-gradient(120deg, var(--accent), var(--accent-2));
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
            margin: 0.4rem 0 1rem;
        }

        h2 {
            text-align: start;
        }
    }

    .topic-table {
        display: flex;
        flex-direction: column;
        border: 1px solid var(--border);
        border-radius: 0.8rem;
        overflow: hidden;
    }

    .topic-row {
        display: grid;
        grid-template-columns: 1fr auto auto;
        gap: 1rem;
        align-items: center;
        padding: 0.7rem 1rem;
        font-size: 0.92rem;
        text-align: start;

        &:not(:last-child) {
            border-bottom: 1px solid var(--border);
        }
    }

    .topic-name {
        font-weight: 600;
    }

    .topic-correct {
        opacity: 0.7;
    }

    .topic-range {
        font-weight: 700;
        color: var(--accent);
    }
</style>
