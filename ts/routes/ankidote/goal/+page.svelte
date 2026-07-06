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
    import { fade, fly, scale } from "svelte/transition";
    import { quintOut } from "svelte/easing";
    import { loadAnkidoteState, saveAnkidoteState } from "../state";
    import {
        Shell,
        Card,
        Badge,
        Button,
        DropperSwitch,
        Beaker,
        Dropper,
        PourFX,
    } from "../_lib";
    import {
        type CommitmentDef,
        defaultCommitments,
        mergePlanCommitments,
    } from "../_lib/commitments";
    import { computePlan, percentile, toScore } from "../_lib/plan";

    // The target + craft flow is a sequence of big, one-at-a-time stages rather
    // than a single stacked form: reveal the current range, then pick the score,
    // weekly time and exam date, then pour each ingredient into the brew, and
    // finally land on the full editable plan. A live HUD tracks total hours and
    // the ANTIcipated range across every stage so toggles show their effect.
    type Step = "reveal" | "score" | "budget" | "date" | "walk" | "plan";
    let phase: Step = "reveal";

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
            // Already crafted before — skip straight to the editable summary.
            phase = "plan";
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

    function todayStr(): string {
        const d = new Date();
        d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
        return d.toISOString().slice(0, 10);
    }
    const minDate = todayStr();

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

    // The opening reveal shows where you stand: the diagnostic range when we
    // have one, otherwise a rough band around the fallback baseline.
    $: revealLow = hasDiagnostic ? diagLow : Math.max(205, baseline - 60);
    $: revealHigh = hasDiagnostic ? diagHigh : Math.min(805, baseline + 60);

    // The craft walkthrough starts from the bare baseline: no efficiency habits
    // on, and cards/problems at their default pace. That way the opening total
    // time / predicted score reflect "nothing added yet", and every ingredient
    // the user pours in visibly moves those numbers.
    let craftReset = false;

    function resetForCraft(): void {
        for (const c of commitments) {
            if (c.rates) {
                c.selected = "focused";
            } else {
                c.enabled = false;
            }
        }
        commitments = commitments;
    }

    // --- craft walkthrough --------------------------------------------------
    // We pour ingredients in one at a time. Cards/problems come first (they set
    // the pace and are required), then the efficiency habits the user can keep
    // or leave out. `walkOrder` is stable because toggling `enabled` never
    // changes membership, only flags.
    let walkIndex = 0;
    let anim: "pour" | "toss" | null = null;

    $: walkOrder = [...quotaCommitments, ...gridCommitments];
    $: current = walkOrder[walkIndex] ?? null;

    // Each ingredient drips in a distinct, vivid colour so no two pours look
    // alike as you build the recipe.
    const POUR_COLORS = [
        "#a3e635", // lime
        "#f59e0b", // amber
        "#38bdf8", // sky
        "#a855f7", // violet
        "#f43f5e", // rose
        "#2dd4bf", // teal
        "#facc15", // gold
        "#4ade80", // green
    ];
    $: curColor = POUR_COLORS[walkIndex % POUR_COLORS.length];

    // The brew's colour crossfades toward the freshly-added ingredient after
    // each addition (animated by Beaker's transitionable --tint).
    let brewTint = "#22c55e";

    // The brew's fill = how comfortably the plan lands inside the exam window.
    // Adding an ingredient shortens the time needed, so the level visibly rises.
    // Kept in a high band (90–100%) so the surface always sits up at the pour
    // point — the drops land in the liquid, not into empty glass below it.
    $: brewFill =
        alreadyThere || weeksNeeded <= 0
            ? 100
            : Math.max(90, Math.min(100, Math.round(90 + (weeksLeft / weeksNeeded) * 10)));

    function startCraft(): void {
        // Begin from the bare baseline (nothing added, default pace), once.
        if (!hasSavedPlan && !craftReset) {
            resetForCraft();
            craftReset = true;
        }
        walkIndex = 0;
        anim = null;
        phase = "walk";
    }

    function advance(): void {
        anim = null;
        if (walkIndex + 1 >= walkOrder.length) {
            phase = "plan";
        } else {
            walkIndex += 1;
        }
    }

    function keepCurrent(): void {
        if (!current || anim) {
            return;
        }
        current.enabled = true;
        commitments = commitments;
        anim = "pour";
        // Hold the brew's colour until the stream actually lands in it, then
        // crossfade toward the freshly-added ingredient. Capture the colour now
        // since `curColor` moves on to the next ingredient once we advance.
        const landed = curColor;
        setTimeout(() => (brewTint = landed), 430);
        setTimeout(advance, 900);
    }

    function dropCurrent(): void {
        if (!current || anim) {
            return;
        }
        current.enabled = false;
        commitments = commitments;
        anim = "toss";
        setTimeout(advance, 720);
    }

    function walkBack(): void {
        if (anim) {
            return;
        }
        if (walkIndex === 0) {
            phase = "budget";
        } else {
            walkIndex -= 1;
        }
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
    {#if phase !== "plan"}
        <div class="wizard">
            {#if phase !== "reveal" && phase !== "walk"}
                <div class="hud" transition:fade={{ duration: 250 }}>
                    <div class="hud-item">
                        <span class="hud-label">Total time</span>
                        <span class="hud-value">{totalHours}<em>hrs</em></span>
                    </div>
                    <div class="hud-sep"></div>
                    <div class="hud-item">
                        <span class="hud-label">Weeks @ {weeklyBudget} hrs/wk</span>
                        <span class="hud-value">{weeksNeeded}<em>wks</em></span>
                    </div>
                    <div class="hud-sep"></div>
                    <div class="hud-item">
                        <span class="hud-label">Predicted by exam</span>
                        <span class="hud-value accent">
                            {predictedLow}&ndash;{predictedHigh}
                        </span>
                    </div>
                </div>
            {/if}

            {#if phase === "reveal"}
                <div
                    class="stage"
                    in:fly={{ y: 26, duration: 520, easing: quintOut }}
                >
                    <Badge variant="green" dot>Diagnostic complete</Badge>
                    <p class="stage-eyebrow">Where you stand today</p>
                    <div class="big-range">
                        {revealLow}<span class="dash">&ndash;</span>{revealHigh}
                    </div>
                    <p class="stage-sub">
                        {#if vague || !hasDiagnostic}
                            This is a <b>vague estimate</b>. Take the diagnostic
                            anytime to anchor it. Let's craft the antidote that
                            moves it.
                        {:else}
                            Your current GMAT range from the diagnostic. Now
                            let's craft the antidote that moves it.
                        {/if}
                    </p>
                    <Button on:click={() => (phase = "date")}>
                        Set my target &rarr;
                    </Button>
                </div>
            {:else if phase === "score"}
                <div
                    class="stage"
                    in:fly={{ y: 26, duration: 460, easing: quintOut }}
                >
                    <p class="stage-eyebrow">Your desired score</p>
                    <div class="big-number">{desiredScore}</div>
                    <p class="stage-pct">~{percentile(desiredScore)}th percentile</p>
                    <input
                        class="big-slider"
                        type="range"
                        min="205"
                        max="805"
                        step="10"
                        bind:value={desiredScore}
                    />
                    <div class="scale wide">
                        <span>205</span>
                        <span>805</span>
                    </div>
                    <div class="stage-nav">
                        <button class="ghost" on:click={() => (phase = "date")}>
                            &larr; back
                        </button>
                        <Button on:click={() => (phase = "budget")}>Next &rarr;</Button>
                    </div>
                </div>
            {:else if phase === "budget"}
                <div
                    class="stage"
                    in:fly={{ y: 26, duration: 460, easing: quintOut }}
                >
                    <p class="stage-eyebrow">Time you'll spend</p>
                    <div class="big-number">
                        {weeklyBudget}<span class="unit">hrs<em>/week</em></span>
                    </div>
                    <input
                        class="big-slider"
                        type="range"
                        min="2"
                        max="25"
                        step="1"
                        bind:value={weeklyBudget}
                    />
                    <div class="scale wide">
                        <span>2 hrs</span>
                        <span>25 hrs</span>
                    </div>
                    <div class="stage-nav">
                        <button class="ghost" on:click={() => (phase = "score")}>
                            &larr; back
                        </button>
                        <Button on:click={startCraft}>
                            Start crafting the brew &rarr;
                        </Button>
                    </div>
                </div>
            {:else if phase === "date"}
                <div
                    class="stage"
                    in:fly={{ y: 26, duration: 460, easing: quintOut }}
                >
                    <p class="stage-eyebrow">Your exam date</p>
                    <input
                        class="big-date"
                        type="date"
                        min={minDate}
                        bind:value={testDate}
                    />
                    <p class="stage-pct">
                        {weeksLeft}
                        {weeksLeft === 1 ? "week" : "weeks"} away
                    </p>
                    <div class="stage-nav">
                        <button class="ghost" on:click={() => (phase = "reveal")}>
                            &larr; back
                        </button>
                        <Button on:click={() => (phase = "score")}>Next &rarr;</Button>
                    </div>
                </div>
            {:else if phase === "walk" && current}
                <div class="stage craft" in:fade={{ duration: 260 }}>
                    {#key walkIndex}
                        <div class="ing-info" in:fade={{ duration: 300 }}>
                            <span
                                class="ing-name"
                                style="color: color-mix(in srgb, {curColor} 45%, #ffffff);"
                            >
                                {current.name}
                            </span>
                            <p class="ing-enforce">{current.enforce}</p>
                        </div>
                    {/key}

                    <div class="craft-main">
                        <div class="hud hud-craft">
                            <div class="hud-item">
                                <span class="hud-label">Total time</span>
                                <span class="hud-value">{totalHours}<em>hrs</em></span>
                            </div>
                            <div class="hud-rule"></div>
                            <div class="hud-item">
                                <span class="hud-label">Weeks @ {weeklyBudget} hrs/wk</span>
                                <span class="hud-value">{weeksNeeded}<em>wks</em></span>
                            </div>
                            <div class="hud-rule"></div>
                            <div class="hud-item">
                                <span class="hud-label">Predicted by exam</span>
                                <span class="hud-value accent">
                                    {predictedLow}&ndash;{predictedHigh}
                                </span>
                            </div>
                        </div>

                        <div class="pour-zone">
                            <div class="ing-slot">
                            {#key walkIndex}
                                <div
                                    class="ing-vial {anim ?? ''}"
                                    in:scale={{
                                        duration: 340,
                                        start: 0.8,
                                        easing: quintOut,
                                    }}
                                >
                                    <Dropper
                                        tint={curColor}
                                        squeezing={anim === "pour"}
                                        width={58}
                                        height={152}
                                    />
                                </div>
                            {/key}
                        </div>

                        {#if anim === "pour"}
                            <div class="fx-slot" out:fade={{ duration: 160 }}>
                                <PourFX tint={curColor} />
                            </div>
                        {/if}

                        <div class="brew">
                            <Beaker
                                tint={brewTint}
                                shape="flask"
                                fill={brewFill}
                                width={186}
                                height={224}
                                seed={4242}
                                ticks={false}
                            />
                            <span class="brew-label">the brew</span>
                        </div>
                        </div>
                    </div>

                    {#if current.rates}
                        <div class="ing-rates">
                            {#each current.rates as r (r.level)}
                                <button
                                    class="rate"
                                    class:active={current.selected === r.level}
                                    on:click={() => setLevel(current, r.level)}
                                    disabled={!!anim}
                                >
                                    <span class="rate-num">
                                        {r.rate}
                                        <em>{current.unit}</em>
                                    </span>
                                    <span class="rate-env">{r.env}</span>
                                </button>
                            {/each}
                        </div>
                        <p class="ing-req">
                            Required. It sets your pace &amp; ANTIcipated score.
                        </p>
                        <div class="ing-actions">
                            <button
                                class="ghost"
                                on:click={walkBack}
                                disabled={!!anim}
                            >
                                &larr; back
                            </button>
                            <Button on:click={keepCurrent} disabled={!!anim}>
                                Pour it in &rarr;
                            </Button>
                        </div>
                    {:else}
                        <div class="ing-actions two">
                            <div class="act">
                                <button
                                    class="toss-btn"
                                    on:click={dropCurrent}
                                    disabled={!!anim}
                                >
                                    Leave it out
                                </button>
                                <span class="delta cost">
                                    +{round1(gapHours * (current.efficiency ?? 0))} hrs
                                </span>
                            </div>
                            <div class="act">
                                <Button on:click={keepCurrent} disabled={!!anim}>
                                    Add to the brew
                                </Button>
                                <span class="delta save">
                                    &minus;{round1(gapHours * (current.efficiency ?? 0))} hrs
                                </span>
                            </div>
                        </div>
                        <button
                            class="ghost small"
                            on:click={walkBack}
                            disabled={!!anim}
                        >
                            &larr; back
                        </button>
                    {/if}
                </div>
            {/if}
        </div>
    {:else}
        <Card>
            <Badge variant="lime" dot>Your Ankidote plan</Badge>
            {#if alreadyThere}
                <h1>You're already at {desiredScore}.</h1>
            {:else}
                <h1>{baseline} &rarr; {desiredScore} in {totalHours} hours.</h1>
            {/if}

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

            {#if !onTrack && !alreadyThere}
                <p class="verdict warn">
                    You're behind. ~{weeksNeeded} wks needed vs your {weeksLeft} wk window.
                    Add more toggles or hours per week to catch up.
                </p>
            {/if}

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
                                    {#if c.cost}<b>{c.cost}</b>:
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
                                    {#if c.cost}<b>{c.cost}</b>:
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
                <Button variant="outline" on:click={() => (phase = "score")}>
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

    /* --- craft wizard --- */
    .wizard {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 1.4rem;
        min-height: 62vh;
    }

    .hud {
        position: sticky;
        top: 0.5rem;
        z-index: 10;
        display: flex;
        align-items: stretch;
        gap: 1rem;
        padding: 0.55rem 1.2rem;
        border-radius: ad.$r-pill;
        @include ad.glass(rgba(15, 21, 18, 0.82));
        box-shadow: 0 12px 30px -14px rgba(0, 0, 0, 0.7);
    }

    .hud-item {
        display: flex;
        flex-direction: column;
        gap: 0.15rem;
        text-align: center;
    }

    .hud-label {
        font-family: ad.$font-mono;
        font-size: 0.58rem;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: ad.$muted;
    }

    .hud-value {
        font-family: ad.$font-heading;
        font-size: 1.2rem;
        font-weight: 700;
        letter-spacing: -0.01em;
        white-space: nowrap;

        em {
            font-family: ad.$font-mono;
            font-style: normal;
            font-size: 0.68rem;
            font-weight: 400;
            color: ad.$muted;
            margin-left: 0.2rem;
        }

        &.accent {
            @include ad.gradient-text(ad.$green, ad.$lime);
        }
    }

    .hud-sep {
        width: 1px;
        background: ad.$border;
    }

    .stage {
        width: 100%;
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
        gap: 0.8rem;
    }

    .stage-eyebrow {
        font-family: ad.$font-mono;
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        color: ad.$muted;
        margin: 0.4rem 0 0;
    }

    .stage-sub {
        font-size: 1rem;
        line-height: 1.6;
        color: ad.$muted;
        max-width: 30rem;
        margin: 0 0 0.6rem;

        b {
            color: ad.$fg;
            font-weight: 600;
        }
    }

    .stage-pct {
        font-family: ad.$font-mono;
        font-size: 0.9rem;
        color: ad.$green;
        margin: 0;
    }

    .big-number,
    .big-range {
        font-family: ad.$font-heading;
        font-weight: 700;
        letter-spacing: -0.03em;
        line-height: 1;
        @include ad.gradient-text(ad.$green, ad.$lime);
        animation: ad-pop 0.5s cubic-bezier(0.22, 1, 0.36, 1) both;
    }

    .big-number {
        font-size: clamp(4rem, 18vw, 7rem);

        .unit {
            font-size: 0.3em;
            letter-spacing: 0;
            -webkit-text-fill-color: initial;
            color: ad.$muted;
            margin-left: 0.3rem;

            em {
                font-style: normal;
            }
        }
    }

    .big-range {
        font-size: clamp(3rem, 13vw, 5.2rem);
        display: flex;
        align-items: center;
        gap: 0.4rem;

        .dash {
            -webkit-text-fill-color: initial;
            color: ad.$muted;
            font-weight: 300;
        }
    }

    .big-slider {
        width: min(28rem, 100%);
        height: 8px;
        margin-top: 0.5rem;
    }

    .scale.wide {
        width: min(28rem, 100%);
    }

    .big-date {
        width: min(20rem, 100%);
        height: 60px;
        font-family: ad.$font-heading;
        font-size: 1.4rem;
        text-align: center;
        margin: 0.6rem 0 0;
    }

    .stage-nav {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 1rem;
        margin-top: 1rem;
        flex-wrap: wrap;
    }

    .ghost {
        background: none;
        border: none;
        padding: 0.4rem 0.6rem;
        font-family: ad.$font-mono;
        font-size: 0.82rem;
        color: ad.$muted;
        cursor: pointer;
        transition: color 0.15s ease;

        &:hover:not(:disabled) {
            color: ad.$fg;
        }
        &:disabled {
            opacity: 0.4;
            cursor: default;
        }
        &.small {
            margin-top: 0.4rem;
        }
    }

    /* --- craft walkthrough --- */
    .craft {
        gap: 0.7rem;
    }


    // Walk step: the metrics tile sits to the left of the brew.
    .craft-main {
        display: flex;
        align-items: flex-end;
        justify-content: center;
        gap: 1.6rem;
        flex-wrap: wrap;
    }

    .hud-craft {
        position: static;
        flex-direction: column;
        align-items: stretch;
        gap: 0;
        min-width: 210px;
        margin-bottom: 40px;
        padding: 1.4rem 1.6rem;
        border-radius: 14px;
        text-align: left;
    }

    .hud-craft .hud-item {
        align-items: flex-start;
        text-align: left;
        padding: 0.7rem 0;
        gap: 0.35rem;
    }

    .hud-craft .hud-label {
        font-size: 0.76rem;
    }

    .hud-craft .hud-value {
        font-size: 2.5rem;
    }

    .hud-craft .hud-value.accent {
        font-size: 2rem;
    }

    .hud-craft .hud-value em {
        font-size: 0.95rem;
        margin-left: 0.3rem;
    }

    .hud-rule {
        height: 1px;
        background: ad.$border;
        margin: 0.2rem 0;
    }

    .ing-info {
        max-width: 30rem;
    }

    .ing-name {
        font-family: ad.$font-heading;
        font-size: clamp(1.5rem, 5vw, 2rem);
        font-weight: 700;
        letter-spacing: -0.02em;
        display: block;
    }

    .ing-enforce {
        font-size: clamp(1.15rem, 3vw, 1.5rem);
        line-height: 1.5;
        font-weight: 600;
        color: ad.$fg;
        margin: 0.6rem 0 0;
    }

    .pour-zone {
        position: relative;
        width: 220px;
        height: 380px;
        margin: 0.4rem auto 0;
    }

    .ing-slot {
        position: absolute;
        top: 0;
        left: 50%;
        transform: translateX(-50%);
        z-index: 3;
    }

    .ing-vial {
        transform-origin: 50% 50%;
        will-change: transform;
    }

    .ing-vial.pour {
        animation: ad-pour-tip 0.9s cubic-bezier(0.4, 0, 0.3, 1);
    }

    .ing-vial.toss {
        animation: ad-toss-away 0.72s cubic-bezier(0.4, 0, 1, 1) forwards;
    }

    .fx-slot {
        position: absolute;
        top: 150px;
        left: 50%;
        width: 120px;
        height: 110px;
        transform: translateX(-50%);
        z-index: 2;
        pointer-events: none;
    }

    .brew {
        position: absolute;
        bottom: 0;
        left: 50%;
        transform: translateX(-50%);
        z-index: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
    }

    .brew-label {
        font-family: ad.$font-mono;
        font-size: 0.66rem;
        text-transform: uppercase;
        letter-spacing: 0.16em;
        color: ad.$muted;
        margin-top: 0.3rem;
    }

    .act {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 0.45rem;
    }

    .delta {
        font-family: ad.$font-mono;
        font-size: 0.85rem;
        font-weight: 600;
        letter-spacing: 0.02em;

        &.save {
            color: ad.$green;
        }
        &.cost {
            color: #e0a758;
        }
    }

    .ing-req {
        font-family: ad.$font-mono;
        font-size: 0.74rem;
        color: ad.$muted;
        margin: 0;
    }

    .ing-rates {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 0.5rem;
        width: min(34rem, 100%);
    }

    .ing-actions {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 1rem;
        margin-top: 0.4rem;
        flex-wrap: wrap;

        &.two {
            gap: 1.2rem;
        }
    }

    .toss-btn {
        padding: 0.7rem 1.3rem;
        border-radius: ad.$r-pill;
        border: 1px solid rgba(224, 167, 88, 0.5);
        background: transparent;
        color: #e0a758;
        font-family: ad.$font-body;
        font-weight: 600;
        font-size: 0.9rem;
        cursor: pointer;
        transition:
            background 0.15s ease,
            box-shadow 0.15s ease;

        &:hover:not(:disabled) {
            background: rgba(224, 167, 88, 0.1);
        }
        &:disabled {
            opacity: 0.4;
            cursor: default;
        }
    }

    @keyframes ad-pop {
        0% {
            opacity: 0;
            transform: translateY(16px) scale(0.9);
        }
        60% {
            opacity: 1;
        }
        100% {
            opacity: 1;
            transform: none;
        }
    }

    // The dropper dips toward the brew as it squeezes, then lifts back — the
    // bulb-squeeze and the drip itself are choreographed inside <Dropper>.
    @keyframes ad-pour-tip {
        0% {
            transform: translateY(0);
        }
        24% {
            transform: translateY(9px);
        }
        44% {
            transform: translateY(6px);
        }
        100% {
            transform: translateY(0);
        }
    }

    @keyframes ad-toss-away {
        0% {
            transform: rotate(0);
            opacity: 1;
        }
        100% {
            transform: translate(150px, -30px) rotate(56deg);
            opacity: 0;
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
        font-size: 1.05rem;
        line-height: 1.45;
        font-weight: 600;
        padding: 0.9rem 1.1rem;
        border-radius: ad.$r-input;
        border: 1px solid rgba(34, 197, 94, 0.45);
        background: ad.$green-wash;
        margin: 0 0 1.2rem;

        &.warn {
            font-size: 1.1rem;
            font-weight: 700;
            color: #ef4444;
            border-color: rgba(239, 68, 68, 0.6);
            background: rgba(239, 68, 68, 0.12);
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
