<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { onMount } from "svelte";
    import { goto } from "$app/navigation";
    import { loadAnkidoteState, loadAnkidoteAuth, ankidoteLogout } from "./state";
    import { Shell, Logo, Button, Badge } from "./_lib";

    let email: string | null = null;

    // Once the diagnostic has been taken the entry point is the dashboard, not
    // a fresh build; the landing page collapses to a single dashboard link.
    let hasDiagnostic = false;
    let loaded = false;

    onMount(async () => {
        const auth = await loadAnkidoteAuth();
        if (!auth.loggedIn) {
            goto("/ankidote/login");
            return;
        }
        email = auth.email ?? null;
        const state = await loadAnkidoteState();
        hasDiagnostic = !!state.diagnostic;
        loaded = true;
    });

    async function signOut(): Promise<void> {
        await ankidoteLogout();
        goto("/ankidote/login");
    }

    function buildIt(): void {
        // Diagnostic first: the goal/plan screen is built from its result.
        goto("/ankidote/diagnostic");
    }

    function viewStats(): void {
        goto("/ankidote/stats");
    }

    const tradeoffs = [
        { cost: "+6 min / session", gain: "mistakes reviewed, not repeated" },
        { cost: "+20 s / question", gain: "every answer choice ranked" },
        { cost: "+15 s / card", gain: "you explain why, so it transfers" },
        { cost: "+25 min / week", gain: "your score range stays honest" },
    ];
</script>

<Shell align="top" max="60rem">
    <section class="hero">
        <div class="orb">
            <span class="ring r1" aria-hidden="true"></span>
            <span class="ring r2" aria-hidden="true"></span>
            <div class="core">
                <Logo size={96} wordmark={false} />
            </div>
            <div class="chip-float f1">
                <Badge variant="green" dot>18 min</Badge>
            </div>
            <div class="chip-float f2">
                <Badge variant="lime">655 &rarr; 705</Badge>
            </div>
        </div>

        <span class="wordmark">
            Anki
            <em>dote</em>
        </span>

        <h1>
            The antidote to
            <br />
            <span class="grad">unstructured studying.</span>
        </h1>

        <p class="sub">
            One dose of diagnostic, a steady drip of the exact cards and problems your
            score needs.
        </p>

        <div class="cta-row">
            {#if loaded && hasDiagnostic}
                <Button size="lg" on:click={viewStats}>View my dashboard</Button>
                <Button
                    size="lg"
                    variant="outline"
                    on:click={() => goto("/ankidote/brew")}
                >
                    My Brew
                </Button>
            {:else if loaded}
                <Button size="lg" on:click={buildIt}>Build your Ankidote</Button>
            {/if}
        </div>

        <div class="chips">
            {#each tradeoffs as t}
                <span class="chip">
                    <b>{t.cost}</b>
                    <span class="arrow">&rarr;</span>
                    {t.gain}
                </span>
            {/each}
        </div>
    </section>

    <footer>
        <p>Every tradeoff above is sourced. Ask to see the receipts.</p>
        {#if loaded}
            <p class="account">
                {#if email}Signed in as {email}.
                {/if}
                <button class="signout" on:click={signOut}>Sign out</button>
            </p>
        {/if}
    </footer>
</Shell>

<style lang="scss">
    @use "./_lib/theme" as ad;

    .hero {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        min-height: 86vh;
    }

    // Hero orb: the vial suspended inside two counter-rotating orbital rings,
    // with live stat badges floating around it (bold choice #2).
    .orb {
        position: relative;
        width: 150px;
        height: 150px;
        margin-bottom: 0.6rem;
        display: grid;
        place-items: center;
    }

    .ring {
        position: absolute;
        border-radius: 50%;
        border: 1px solid rgba(34, 197, 94, 0.25);
    }
    .r1 {
        inset: 0;
        border-top-color: ad.$green;
        border-right-color: rgba(163, 230, 53, 0.5);
        @include ad.motion-safe {
            animation: ad-spin 10s linear infinite;
        }
    }
    .r2 {
        inset: 22px;
        border-bottom-color: ad.$lime;
        border-left-color: rgba(34, 197, 94, 0.4);
        @include ad.motion-safe {
            animation: ad-spin 15s linear infinite reverse;
        }
    }
    .core {
        filter: drop-shadow(0 0 30px rgba(34, 197, 94, 0.45));
        @include ad.motion-safe {
            animation: ad-float 8s ease-in-out infinite;
        }
    }

    .chip-float {
        position: absolute;
    }
    .f1 {
        top: 4%;
        right: -18%;
        @include ad.motion-safe {
            animation: ad-float 4s ease-in-out infinite;
        }
    }
    .f2 {
        bottom: 8%;
        left: -14%;
        @include ad.motion-safe {
            animation: ad-float 5s ease-in-out infinite 0.6s;
        }
    }

    @keyframes ad-spin {
        to {
            transform: rotate(360deg);
        }
    }
    @keyframes ad-float {
        0%,
        100% {
            transform: translateY(0);
        }
        50% {
            transform: translateY(-12px);
        }
    }

    .wordmark {
        font-family: ad.$font-heading;
        font-size: 1.6rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        margin-bottom: 1.4rem;

        em {
            font-style: normal;
            @include ad.gradient-text(ad.$green, ad.$lime);
        }
    }

    h1 {
        font-family: ad.$font-heading;
        font-size: clamp(2.4rem, 6vw, 4rem);
        font-weight: 700;
        line-height: 1.08;
        letter-spacing: -0.03em;
        margin: 0 0 1.2rem;

        .grad {
            @include ad.gradient-text(ad.$green, ad.$lime);
        }
    }

    .sub {
        max-width: 34rem;
        font-size: 1.1rem;
        line-height: 1.6;
        color: ad.$muted;
        margin: 0 0 2.2rem;
    }

    .cta-row {
        display: flex;
        gap: 0.9rem;
        flex-wrap: wrap;
        justify-content: center;
        margin-bottom: 2.6rem;
        min-height: 44px;
    }

    // Four tradeoff tiles laid out as 2 rows of 2.
    .chips {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 0.6rem;
        max-width: 40rem;
        width: 100%;
    }

    .chip {
        display: flex;
        align-items: center;
        justify-content: center;
        flex-wrap: wrap;
        text-align: center;
        font-family: ad.$font-mono;
        font-size: 0.76rem;
        letter-spacing: 0.02em;
        padding: 0.6rem 0.9rem;
        border-radius: ad.$r-card-sm;
        color: ad.$muted;
        @include ad.glass(rgba(255, 255, 255, 0.04));

        b {
            color: ad.$green;
            font-weight: 500;
        }
        .arrow {
            color: ad.$lime;
            margin: 0 0.15rem;
        }
    }

    footer {
        text-align: center;
        padding: 2rem 1.5rem 0;
        font-size: 0.85rem;
        color: ad.$muted;

        p {
            margin: 0.3rem 0;
        }
    }

    .signout {
        background: none;
        border: none;
        color: ad.$green;
        font: inherit;
        font-weight: 600;
        cursor: pointer;
        padding: 0;

        &:hover {
            text-decoration: underline;
        }
    }
</style>
