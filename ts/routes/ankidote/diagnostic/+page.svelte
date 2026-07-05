<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    // Talks to the Python CAT/IRT engine over the mediasrv JSON endpoints
    // (AntiPlan/diagnostic-cat-plan.md §4.4). The auth header is injected for
    // this trusted webview by AuthInterceptor, and application/binary is
    // required by the API's CSRF guard even for a JSON body.
    import { onMount } from "svelte";
    import { goto } from "$app/navigation";
    import { loadAnkidoteAuth, loadAnkidoteState, saveAnkidoteState } from "../state";
    import type { AnkidoteScoreSnapshot } from "../state";
    import { runDiagnostic, type NextDiagnosticQuestionResponse } from "../engine";
    import type { DiagnosticQuestion } from "@generated/anki/ankidote_pb";
    import { Shell, Card, Badge, Button } from "../_lib";

    type Phase = "intro" | "question" | "done";

    let phase: Phase = "intro";
    let loading = false;
    let errorMessage = "";

    // The diagnostic runner is stateless: we keep the cumulative answers and
    // the backend re-derives the adaptive session deterministically.
    let answers: { questionId: bigint; chosenChoice: number }[] = [];
    let question: DiagnosticQuestion | undefined;
    let selectedChoice: number | null = null;
    let latest: NextDiagnosticQuestionResponse | undefined;

    // The diagnostic is only available to signed-in users; signed-out visitors
    // are sent back to the login screen to start the flow from the top.
    onMount(async () => {
        const auth = await loadAnkidoteAuth();
        if (!auth.loggedIn) {
            goto("/ankidote/login");
        }
    });

    function apply(state: NextDiagnosticQuestionResponse): void {
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
        answers = [];
        try {
            apply(await runDiagnostic({ answers, confidence: {}, maxQuestions: 0 }));
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
        answers = [
            ...answers,
            { questionId: question.id, chosenChoice: selectedChoice },
        ];
        try {
            apply(await runDiagnostic({ answers, confidence: {}, maxQuestions: 0 }));
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
                topicScores: latest.topicScores.map((t) => ({
                    topic: t.topic,
                    theta: t.theta,
                    standardError: t.standardError,
                    score: t.score
                        ? { low: t.score.low, high: t.score.high }
                        : undefined,
                    questionsAnswered: t.questionsAnswered,
                    questionsCorrect: t.questionsCorrect,
                })),
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
            // Append a score snapshot so the My Brew page can plot a real
            // progress trend as diagnostics accumulate. Merge with the existing
            // history (the backend merges top-level keys, not arrays).
            const prev = await loadAnkidoteState();
            const snapshot: AnkidoteScoreSnapshot = {
                ts: diagnostic.takenAt,
                low: diagnostic.low,
                high: diagnostic.high,
                baseline,
            };
            const scoreHistory = [...(prev.scoreHistory ?? []), snapshot];
            await saveAnkidoteState({ diagnostic, scoreHistory });
        }
        goto("/ankidote/goal");
    }

    // Let impatient users go straight to planning. We seed a deliberately wide,
    // unmeasured baseline flagged `vague` so the goal screen can warn that the
    // starting score is a placeholder until a real diagnostic is taken.
    async function skipDiagnostic(): Promise<void> {
        loading = true;
        const diagnostic = {
            low: 355,
            high: 655,
            baseline: 505,
            answered: 0,
            topicScores: [],
            takenAt: Date.now(),
            vague: true,
        };
        try {
            sessionStorage.setItem(
                "ankidote.diagnostic",
                JSON.stringify(diagnostic),
            );
        } catch {
            // sessionStorage may be unavailable; goal screen falls back.
        }
        // Deliberately do not push a score snapshot: a vague placeholder would
        // pollute the My Brew progress trend, which should only reflect real
        // measurements.
        await saveAnkidoteState({ diagnostic });
        goto("/ankidote/goal");
    }
</script>

<Shell align="top" max="44rem">
    {#if phase === "intro"}
        <Card>
            <div class="intro">
                <Badge variant="green" dot>Ankidote diagnostic</Badge>
                <h1>Find out where you stand.</h1>
                <p class="sub">
                    An adaptive test: each question is picked to teach us the most about
                    your ability, so it stays short. You'll get a GMAT score range,
                    overall and per topic, not a false precision point
                    score.
                </p>
                <ul class="facts">
                    <li>
                        <b>Adaptive length</b>: it stops as soon as your score range is tight enough
                    </li>
                    <li>
                        <b>Quant, Verbal, Data Insights</b>: all three sections
                    </li>
                    <li>
                        <b>No going back</b>: just like test day
                    </li>
                </ul>
                <Button on:click={begin} disabled={loading}>
                    {loading ? "Preparing…" : "Start the diagnostic"}
                </Button>
                <div class="skip">
                    <button
                        class="skip-link"
                        on:click={skipDiagnostic}
                        disabled={loading}
                    >
                        Skip for now &rarr;
                    </button>
                    <p class="skip-warn">
                        Heads up: skipping means your starting score is an
                        <b>extremely vague estimate</b>, not a measurement. Your
                        plan and predictions stay rough until you take the
                        diagnostic. You can do it anytime.
                    </p>
                </div>
                {#if errorMessage}
                    <p class="error">{errorMessage}</p>
                {/if}
            </div>
        </Card>
    {:else if phase === "question" && question && latest}
        <Card>
            <header class="status">
                <div class="progress-track">
                    <div class="progress-fill" style="width: {progress * 100}%"></div>
                </div>
                <div class="status-row">
                    <span class="counter">
                        Question {latest.answered + 1} · adaptive
                    </span>
                    {#if latest.answered > 0}
                        <span class="score-chip">
                            current range <b>{scoreLabel}</b>
                        </span>
                    {/if}
                </div>
            </header>

            <div class="tags">
                <Badge variant="green">{sectionNames[question.section] ?? ""}</Badge>
                <Badge variant="outline">{question.topic}</Badge>
                <Badge variant="muted">{question.subtopic}</Badge>
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
                <Button
                    on:click={confirmAnswer}
                    disabled={selectedChoice === null || loading}
                >
                    {loading ? "Scoring…" : "Confirm answer"}
                </Button>
            </div>
            {#if errorMessage}
                <p class="error">{errorMessage}</p>
            {/if}
        </Card>
    {:else if phase === "done" && latest}
        <Card>
            <div class="results">
                <Badge variant="lime" dot>Diagnostic complete</Badge>
                <h1>Your current score range</h1>
                <div class="big-score">{scoreLabel}</div>
                <p class="sub">
                    Based on {latest.answered} adaptive questions. The band narrows as Ankidote
                    gathers more evidence. Certainty is earned, not assumed.
                </p>
            </div>

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
                <Button variant="outline" on:click={begin} disabled={loading}>
                    Retake diagnostic
                </Button>
                <Button on:click={goToGoal} disabled={loading}>
                    Set your goal &rarr;
                </Button>
            </div>
        </Card>
    {/if}
</Shell>

<style lang="scss">
    @use "../_lib/theme" as ad;

    h1 {
        font-family: ad.$font-heading;
        font-size: clamp(1.6rem, 4vw, 2.2rem);
        font-weight: 700;
        letter-spacing: -0.02em;
        margin: 1rem 0 0.8rem;
    }

    h2 {
        font-family: ad.$font-heading;
        font-size: 1.05rem;
        font-weight: 600;
        margin: 2rem 0 0.8rem;
    }

    .sub {
        font-size: 1rem;
        line-height: 1.6;
        color: ad.$muted;
        margin: 0 0 1.4rem;
    }

    .facts {
        list-style: none;
        padding: 0;
        margin: 0 0 1.8rem;

        li {
            padding: 0.55rem 0;
            border-bottom: 1px solid ad.$border;
            font-size: 0.95rem;
            color: ad.$muted;

            &:last-child {
                border-bottom: none;
            }

            b {
                color: ad.$fg;
                font-weight: 600;
            }
        }
    }

    .status {
        margin-bottom: 1.4rem;
    }

    .progress-track {
        height: 6px;
        border-radius: ad.$r-pill;
        background: rgba(255, 255, 255, 0.08);
        overflow: hidden;
        margin-bottom: 0.7rem;
    }

    .progress-fill {
        height: 100%;
        border-radius: ad.$r-pill;
        background: linear-gradient(to right, ad.$serum, ad.$green);
        box-shadow: 0 0 12px rgba(34, 197, 94, 0.5);
        transition: width 0.3s ease;
    }

    .status-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-family: ad.$font-mono;
        font-size: 0.78rem;
        letter-spacing: 0.04em;
        color: ad.$muted;
    }

    .score-chip {
        b {
            color: ad.$green;
            margin-left: 0.3rem;
        }
    }

    .tags {
        display: flex;
        gap: 0.45rem;
        flex-wrap: wrap;
        margin-bottom: 1.2rem;
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
        gap: 0.6rem;
    }

    .choice {
        display: flex;
        align-items: center;
        gap: 0.85rem;
        width: 100%;
        text-align: start;
        font-family: ad.$font-body;
        font-size: 0.98rem;
        line-height: 1.45;
        padding: 0.8rem 0.95rem;
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
        flex-shrink: 0;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.8rem;
        height: 1.8rem;
        border-radius: 50%;
        border: 1px solid ad.$border;
        font-family: ad.$font-mono;
        font-size: 0.8rem;
        font-weight: 500;
    }

    .actions {
        margin-top: 1.6rem;
        display: flex;
        justify-content: flex-end;
    }

    .actions.split {
        justify-content: space-between;
        align-items: center;
        gap: 1rem;
    }

    .error {
        margin-top: 1rem;
        color: ad.$danger;
        font-size: 0.9rem;
    }

    .skip {
        margin-top: 1.1rem;
    }

    .skip-link {
        background: none;
        border: none;
        padding: 0;
        font-family: ad.$font-mono;
        font-size: 0.82rem;
        letter-spacing: 0.03em;
        color: ad.$muted;
        cursor: pointer;
        transition: color 0.15s ease;

        &:hover:not(:disabled) {
            color: ad.$fg;
        }

        &:disabled {
            opacity: 0.5;
            cursor: default;
        }
    }

    .skip-warn {
        margin: 0.55rem 0 0;
        padding: 0.7rem 0.85rem;
        border: 1px solid ad.$border;
        border-left: 2px solid #e0a758;
        border-radius: ad.$r-input;
        background: rgba(0, 0, 0, 0.2);
        font-size: 0.82rem;
        line-height: 1.5;
        color: ad.$muted;

        b {
            color: #e0a758;
            font-weight: 600;
        }
    }

    .results {
        text-align: center;

        .big-score {
            font-family: ad.$font-heading;
            font-size: clamp(2.8rem, 9vw, 4.4rem);
            font-weight: 700;
            letter-spacing: -0.03em;
            @include ad.gradient-text(ad.$green, ad.$lime);
            margin: 0.4rem 0 1rem;
        }
    }

    .topic-table {
        display: flex;
        flex-direction: column;
        border: 1px solid ad.$border;
        border-radius: ad.$r-card-sm;
        overflow: hidden;
    }

    .topic-row {
        display: grid;
        grid-template-columns: 1fr auto auto;
        gap: 1rem;
        align-items: center;
        padding: 0.75rem 1rem;
        font-size: 0.92rem;
        text-align: start;

        &:not(:last-child) {
            border-bottom: 1px solid ad.$border;
        }
    }

    .topic-name {
        font-weight: 600;
    }

    .topic-correct {
        font-family: ad.$font-mono;
        font-size: 0.82rem;
        color: ad.$muted;
    }

    .topic-range {
        font-family: ad.$font-mono;
        font-weight: 500;
        color: ad.$green;
    }
</style>
