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
    import { Shell, Card, Badge, Button, DropperSwitch } from "../_lib";
    import {
        type CommitmentDef,
        defaultCommitments,
        mergePlanCommitments,
    } from "../_lib/commitments";
    import { computePlan, percentile, toScore } from "../_lib/plan";

    let phase: "goal" | "plan" = "goal";

    let desiredScore = 645;
    let weeklyBudget = 8;
    let testDate = defaultTestDate();

    // Baseline (point estimate) from the diagnostic that ran first. Falls back
    // to the middle of the scale if the user reached this screen directly.
    const FALLBACK_BASELINE = 505;
    let baseline = FALLBACK_BASELINE;
    let hasDiagnostic = false;
    // True when the diagnostic was skipped: the baseline is an unmeasured
    // placeholder, so we warn and treat it like "no diagnostic" for the target.
    let vague = false;
    // True once a saved plan has been rehydrated, so the diagnostic default
    // target below doesn't clobber the score the user already chose.
    let hasSavedPlan = false;
    let diagLow = 0;
    let diagHigh = 0;

    function applyDiagnostic(d: {
        baseline?: number;
        low?: number;
        high?: number;
        vague?: boolean;
    }): boolean {
        if (typeof d?.baseline === "number") {
            baseline = d.baseline;
            diagLow = d.low ?? d.baseline;
            diagHigh = d.high ?? d.baseline;
            vague = d.vague === true;
            hasDiagnostic = true;
            // Default the target a bit above the measured baseline, unless the
            // user already saved a plan with a chosen target.
            if (!hasSavedPlan) {
                desiredScore = toScore(Math.min(baseline + 100, 805));
            }
            return true;
        }
        return false;
    }

    onMount(async () => {
        // Prefer the persisted/synced diagnostic; fall back to the in-session
        // sessionStorage copy if the backend has nothing yet.
        const state = await loadAnkidoteState();
        // Rehydrate a previously-saved plan so returning here shows the goal,
        // budget and commitment choices the user already committed to (rather
        // than resetting them to defaults).
        if (state.plan) {
            hasSavedPlan = true;
            if (typeof state.plan.desiredScore === "number") {
                desiredScore = state.plan.desiredScore;
            }
            if (typeof state.plan.weeklyBudget === "number") {
                weeklyBudget = state.plan.weeklyBudget;
            }
            if (state.plan.testDate) {
                testDate = state.plan.testDate;
            }
            commitments = mergePlanCommitments(
                defaultCommitments(),
                state.plan.commitments,
            );
        }
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

    let commitments: CommitmentDef[] = defaultCommitments();

    // Which card's reasoning popover is open (only one at a time).
    let openInfoId: string | null = null;
    function toggleInfo(id: string): void {
        openInfoId = openInfoId === id ? null : id;
    }

    // Flashcards/problems keep their own full-width cards (they carry quota
    // sliders); everything else is a compact toggle grid.
    $: quotaCommitments = commitments.filter((c) => c.rates);
    $: gridCommitments = commitments.filter((c) => !c.rates);

    function setLevel(c: CommitmentDef, level: string): void {
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

    // --- live plan estimate ------------------------------------------------
    // All effort-curve / prediction math lives in _lib/plan.ts so the goal
    // screen and the My Brew page stay in lockstep.
    $: est = computePlan({
        desiredScore,
        weeklyBudget,
        testDate,
        baseline,
        commitments,
    });
    $: ({
        weeksLeft,
        gapHours,
        totalHours,
        weeksNeeded,
        onTrack,
        predictedLow,
        predictedHigh,
        alreadyThere,
    } = est);

    // Auto-tune the plan the first time it's built: switch on only as many
    // habits as are needed to land the desired score inside the exam window,
    // most-effective first. If even every habit isn't enough, they all stay on.
    // Only the efficiency habits are toggleable — cards/problems set pace and
    // are always required.
    let autoTuned = false;

    function autoEnableCommitments(): void {
        const toggles = commitments.filter((c) => !c.rates);
        for (const c of toggles) {
            c.enabled = false;
        }
        const estimate = () =>
            computePlan({ desiredScore, weeklyBudget, testDate, baseline, commitments });

        let e = estimate();
        if (!e.onTrack && !e.alreadyThere) {
            const ordered = [...toggles].sort(
                (a, b) => (b.efficiency ?? 0) - (a.efficiency ?? 0),
            );
            for (const c of ordered) {
                c.enabled = true;
                e = estimate();
                if (e.onTrack || e.alreadyThere) {
                    break;
                }
            }
        }
        commitments = commitments;
    }

    function toPlan(): void {
        // Respect an already-saved plan's choices; only tune a fresh plan, once.
        if (!hasSavedPlan && !autoTuned) {
            autoEnableCommitments();
            autoTuned = true;
        }
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

<Shell align="top" max="48rem">
    {#if phase === "goal"}
        <Card>
            <Badge variant="green" dot>Set your target</Badge>
            <h1>What score, and what's it worth in hours?</h1>
            <p class="sub">
                Pick where you want to land and how much time you'll spend. Your
                required hours are measured up from your diagnostic — so the closer you
                already are, the less it costs.
            </p>

            <div class="baseline" class:estimated={!hasDiagnostic || vague}>
                {#if hasDiagnostic && !vague}
                    <span class="baseline-label">Your diagnostic baseline</span>
                    <span class="baseline-value">
                        {baseline}
                        <em>({diagLow}–{diagHigh})</em>
                    </span>
                {:else}
                    <span class="baseline-label">
                        {vague ? "Diagnostic skipped" : "No diagnostic yet"}
                    </span>
                    <span class="baseline-value">
                        ~{baseline}
                        <em>vague estimate</em>
                    </span>
                {/if}
            </div>

            {#if vague || !hasDiagnostic}
                <p class="vague-warn">
                    Your starting score is an <b>extremely vague estimate</b>, not
                    a measurement, so your plan and predictions stay rough.
                    <a href="/ankidote/diagnostic">Take the diagnostic</a>
                    anytime to anchor your baseline and tighten every number.
                </p>
            {/if}

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
                <Button on:click={toPlan}>See my plan &rarr;</Button>
            </div>
        </Card>
    {:else}
        <Card>
            <Badge variant="lime" dot>Your Ankidote plan</Badge>
            {#if alreadyThere}
                <h1>You're already at {desiredScore}.</h1>
            {:else}
                <h1>{baseline} &rarr; {desiredScore} in {totalHours} hours.</h1>
            {/if}
            <p class="sub compact">
                Hours are measured from your diagnostic. Kept habits shave hours off;
                cards &amp; problems set your pace and ANTIcipated score. Tap
                <span class="inline-i">i</span>
                for the why.
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
                    <div class="commit quota-card">
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

                        <div class="commit-head">
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
                    <div class="commit" class:off={!c.enabled}>
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

                        <div class="commit-head">
                            <DropperSwitch
                                bind:checked={c.enabled}
                                label={c.name}
                                on:change={() => (commitments = commitments)}
                            />
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
                <Button variant="outline" on:click={() => (phase = "goal")}>
                    &larr; Adjust target
                </Button>
                <Button on:click={savePlan}>Lock in my plan &rarr;</Button>
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

    .sub {
        font-size: 1rem;
        line-height: 1.6;
        color: ad.$muted;
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
        border: 1px solid ad.$border;
        font-family: ad.$font-mono;
        font-size: 0.68rem;
        font-weight: 500;
        vertical-align: middle;
    }

    /* --- goal inputs --- */
    .baseline {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 0.8rem;
        padding: 0.8rem 1rem;
        margin-bottom: 1.6rem;
        border: 1px solid rgba(34, 197, 94, 0.5);
        border-radius: ad.$r-card-sm;
        background: ad.$green-wash;

        &.estimated {
            border-color: ad.$border;
            background: transparent;
        }
    }

    .baseline-label {
        font-family: ad.$font-mono;
        font-size: 0.72rem;
        font-weight: 500;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: ad.$muted;
    }

    .baseline-value {
        font-family: ad.$font-heading;
        font-size: 1.5rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: ad.$green;

        em {
            font-family: ad.$font-mono;
            font-size: 0.78rem;
            font-style: normal;
            font-weight: 400;
            color: ad.$muted;
        }
    }

    .vague-warn {
        margin: -0.9rem 0 1.6rem;
        padding: 0.7rem 0.9rem;
        border: 1px solid ad.$border;
        border-left: 2px solid #e0a758;
        border-radius: ad.$r-input;
        background: rgba(224, 167, 88, 0.08);
        font-size: 0.84rem;
        line-height: 1.5;
        color: ad.$muted;

        b {
            color: #e0a758;
            font-weight: 600;
        }

        a {
            color: ad.$green;
            font-weight: 600;
            text-decoration: none;

            &:hover {
                text-decoration: underline;
            }
        }
    }

    .field {
        margin-bottom: 1.6rem;
    }

    .field-head {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 0.6rem;
    }

    label {
        font-weight: 600;
        font-size: 0.95rem;
    }

    .field-value {
        font-family: ad.$font-heading;
        font-size: 1.35rem;
        font-weight: 700;
        letter-spacing: -0.01em;

        em {
            font-family: ad.$font-mono;
            font-style: normal;
            font-size: 0.78rem;
            font-weight: 400;
            color: ad.$muted;
        }
    }

    input[type="range"] {
        width: 100%;
        accent-color: ad.$green;
        cursor: pointer;
    }

    .scale {
        display: flex;
        justify-content: space-between;
        font-family: ad.$font-mono;
        font-size: 0.72rem;
        color: ad.$muted;
        opacity: 0.7;
        margin-top: 0.3rem;
    }

    input[type="date"] {
        width: 100%;
        height: 48px;
        padding: 0.5rem 1rem;
        border: 1px solid transparent;
        border-bottom: 2px solid rgba(255, 255, 255, 0.2);
        border-radius: ad.$r-input ad.$r-input 0 0;
        background: rgba(0, 0, 0, 0.5);
        color: ad.$fg;
        font-family: ad.$font-body;
        font-size: 0.95rem;

        &:focus-visible {
            outline: none;
            border-bottom-color: ad.$green;
            box-shadow: 0 10px 20px -10px rgba(34, 197, 94, 0.3);
        }
    }

    /* --- plan stats --- */
    .stats {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 0.8rem;
        margin-bottom: 1rem;
    }

    .stat {
        border: 1px solid ad.$border;
        border-radius: ad.$r-card-sm;
        padding: 0.8rem 1rem;
        display: flex;
        flex-direction: column;
        gap: 0.35rem;
        background: rgba(255, 255, 255, 0.02);
    }

    .stat-label {
        font-family: ad.$font-mono;
        font-size: 0.68rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: ad.$muted;
    }

    .stat-value {
        font-family: ad.$font-heading;
        font-size: 1.7rem;
        font-weight: 700;
        letter-spacing: -0.02em;

        em {
            font-family: ad.$font-mono;
            font-style: normal;
            font-size: 0.82rem;
            font-weight: 400;
            color: ad.$muted;
        }

        &.accent {
            @include ad.gradient-text(ad.$green, ad.$lime);
        }

        &.range {
            font-size: 1.35rem;
            white-space: nowrap;
        }
    }

    .hours-input {
        width: 3.2rem;
        font-family: ad.$font-heading;
        font-size: 1.7rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: ad.$fg;
        background: transparent;
        border: none;
        border-bottom: 2px solid ad.$green;
        padding: 0 0.1rem;
        text-align: center;

        &:focus {
            outline: none;
            border-bottom-color: ad.$lime;
        }
    }

    .verdict {
        font-size: 0.88rem;
        line-height: 1.5;
        padding: 0.65rem 0.95rem;
        border-radius: ad.$r-input;
        border: 1px solid rgba(34, 197, 94, 0.45);
        background: ad.$green-wash;
        margin: 0 0 1.2rem;

        &.warn {
            border-color: rgba(217, 164, 65, 0.6);
            background: rgba(217, 164, 65, 0.12);
        }
    }

    /* --- commitments --- */
    .quota-row {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.7rem;
        margin-bottom: 0.7rem;
    }

    .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr));
        gap: 0.7rem;
    }

    .commit {
        position: relative;
        overflow: visible;
        display: flex;
        flex-direction: column;
        gap: 0.6rem;
        padding: 0.85rem 0.9rem;
        border: 1px solid ad.$border;
        border-radius: ad.$r-card-sm;
        background: rgba(255, 255, 255, 0.02);
        transition:
            border-color 0.2s ease,
            box-shadow 0.2s ease,
            opacity 0.2s ease;

        &.off {
            opacity: 0.55;
        }
        &:not(.off) {
            border-color: rgba(34, 197, 94, 0.4);
            box-shadow: 0 0 24px -14px rgba(34, 197, 94, 0.5);
        }
    }

    .commit-head {
        display: flex;
        align-items: center;
        gap: 0.55rem;
        padding-right: 1.6rem; /* room for the info button */
    }

    .req {
        font-family: ad.$font-mono;
        font-size: 0.6rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        padding: 0.15rem 0.5rem;
        border-radius: ad.$r-pill;
        color: ad.$green;
        border: 1px solid rgba(34, 197, 94, 0.45);
    }

    .info {
        position: absolute;
        top: 0.55rem;
        right: 0.55rem;
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
        border: 1px solid ad.$border;
        background: transparent;
        color: ad.$muted;
        font-family: ad.$font-mono;
        font-size: 0.72rem;
        font-weight: 500;
        line-height: 1;
        cursor: pointer;
        transition:
            border-color 0.15s ease,
            color 0.15s ease,
            box-shadow 0.15s ease;

        &:hover,
        &.active {
            border-color: ad.$green;
            color: ad.$green;
            box-shadow: 0 0 14px -4px rgba(34, 197, 94, 0.6);
        }
    }

    .popover {
        position: absolute;
        top: 2.1rem;
        right: 0.5rem;
        z-index: 20;
        width: 17rem;
        max-width: calc(100vw - 4rem);
        text-align: start;
        padding: 0.9rem 1rem;
        border-radius: ad.$r-card-sm;
        @include ad.glass(rgba(15, 21, 18, 0.9));
        box-shadow: 0 18px 44px -12px rgba(0, 0, 0, 0.6);
    }

    .name {
        font-weight: 600;
        font-size: 0.92rem;
        line-height: 1.25;
    }

    .chip {
        font-family: ad.$font-mono;
        font-size: 0.7rem;
        font-weight: 500;
        letter-spacing: 0.02em;
        padding: 0.22rem 0.6rem;
        border-radius: ad.$r-pill;
        white-space: nowrap;
        align-self: flex-start;

        &.on {
            color: ad.$green;
            border: 1px solid rgba(34, 197, 94, 0.55);
        }
        &.cost {
            color: #e0a758;
            border: 1px solid rgba(224, 167, 88, 0.5);
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
        color: ad.$muted;

        b {
            color: ad.$green;
        }
    }

    .rates {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 0.45rem;
    }

    .rate {
        display: flex;
        flex-direction: column;
        gap: 0.35rem;
        text-align: start;
        padding: 0.6rem 0.65rem;
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

        &.active {
            border-color: ad.$green;
            background: ad.$green-wash;
            box-shadow: 0 0 20px -10px rgba(34, 197, 94, 0.6);
        }
    }

    .rate-num {
        font-family: ad.$font-heading;
        font-size: 1.2rem;
        font-weight: 700;
        letter-spacing: -0.01em;

        em {
            display: block;
            font-family: ad.$font-mono;
            font-style: normal;
            font-size: 0.64rem;
            font-weight: 400;
            letter-spacing: 0.02em;
            color: ad.$muted;
        }
    }

    .rate-env {
        font-size: 0.72rem;
        line-height: 1.3;
        color: ad.$muted;
    }

    /* --- actions --- */
    .actions {
        margin-top: 1.6rem;
        display: flex;
        justify-content: flex-end;

        &.split {
            justify-content: space-between;
            align-items: center;
            gap: 0.8rem;
            flex-wrap: wrap;
        }
    }
</style>
