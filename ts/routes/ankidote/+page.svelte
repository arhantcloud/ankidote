<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import { onMount } from "svelte";
    import { goto } from "$app/navigation";
    import { loadAnkidoteState } from "./state";

    // Once the diagnostic has been taken the entry point is the dashboard, not
    // a fresh build; the landing page collapses to a single dashboard link.
    let hasDiagnostic = false;
    let loaded = false;

    onMount(async () => {
        const state = await loadAnkidoteState();
        hasDiagnostic = !!state.diagnostic;
        loaded = true;
    });

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

<main class="ankidote">
    <section class="hero">
        <div class="glow"></div>

        <div class="logo">
            <svg viewBox="0 0 96 96" aria-hidden="true" class="vial">
                <defs>
                    <linearGradient id="liquid" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stop-color="#63c06f" />
                        <stop offset="100%" stop-color="#2e7d46" />
                    </linearGradient>
                    <clipPath id="flask-clip">
                        <path
                            d="M40 14 h16 v18 l17 30 a10 10 0 0 1 -8.7 15 H31.7
                               a10 10 0 0 1 -8.7 -15 l17 -30 z"
                        />
                    </clipPath>
                </defs>

                <!-- flask outline -->
                <path
                    class="glass"
                    d="M40 14 h16 v18 l17 30 a10 10 0 0 1 -8.7 15 H31.7
                       a10 10 0 0 1 -8.7 -15 l17 -30 z"
                />

                <!-- liquid + bubbles, clipped to the flask -->
                <g clip-path="url(#flask-clip)">
                    <rect x="14" y="52" width="68" height="46" fill="url(#liquid)" />
                    <path
                        d="M14 53 q8.5 -5 17 0 t17 0 t17 0 t17 0 v45 h-68 z"
                        fill="url(#liquid)"
                        opacity="0.65"
                    />
                    <circle class="bubble b1" cx="40" cy="72" r="2.6" />
                    <circle class="bubble b2" cx="52" cy="78" r="1.8" />
                    <circle class="bubble b3" cx="47" cy="68" r="1.3" />
                </g>

                <!-- cork -->
                <rect x="37" y="6" width="22" height="9" rx="3" class="cork" />

                <!-- antidote cross -->
                <g class="cross" transform="translate(66, 20)">
                    <rect x="-3" y="-9" width="6" height="18" rx="2" />
                    <rect x="-9" y="-3" width="18" height="6" rx="2" />
                </g>
            </svg>
            <span class="wordmark">
                Anki
                <em>dote</em>
            </span>
        </div>

        <h1>
            The antidote to
            <br />
            unstructured studying.
        </h1>

        <p class="sub">
            One dose of diagnostic, a steady drip of the exact cards and problems your
            score needs.
        </p>

        <div class="cta-row">
            {#if loaded && hasDiagnostic}
                <button class="cta" on:click={viewStats}>View my dashboard</button>
            {:else if loaded}
                <button class="cta" on:click={buildIt}>Build your Ankidote</button>
            {/if}
        </div>

        <div class="chips">
            {#each tradeoffs as t}
                <span class="chip">
                    <b>{t.cost}</b>
                    &rarr; {t.gain}
                </span>
            {/each}
        </div>
    </section>

    <footer>
        <p>Every tradeoff above is sourced. Ask to see the receipts.</p>
    </footer>
</main>

<style lang="scss">
    .ankidote {
        --accent: #45a05a;
        --accent-2: #2e7d46;
        min-height: 100vh;
        color: var(--fg);
        overflow-x: hidden;
    }

    .hero {
        position: relative;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        min-height: 92vh;
        padding: 3rem 1.5rem 4rem;
    }

    .glow {
        position: absolute;
        top: 6%;
        left: 50%;
        width: 42rem;
        height: 42rem;
        transform: translateX(-50%);
        background: radial-gradient(
            circle,
            rgba(69, 160, 90, 0.2) 0%,
            rgba(46, 125, 70, 0.07) 45%,
            transparent 70%
        );
        pointer-events: none;
    }

    .logo {
        position: relative;
        display: flex;
        align-items: center;
        gap: 0.9rem;
        margin-bottom: 2.2rem;
    }

    .vial {
        width: 64px;
        height: 64px;
        filter: drop-shadow(0 4px 14px rgba(69, 160, 90, 0.3));

        .glass {
            fill: color-mix(in srgb, var(--canvas) 60%, transparent);
            stroke: var(--fg);
            stroke-opacity: 0.55;
            stroke-width: 2.5;
            stroke-linejoin: round;
        }

        .cork {
            fill: var(--fg);
            opacity: 0.75;
        }

        .cross rect {
            fill: var(--accent);
        }

        .bubble {
            fill: rgba(255, 255, 255, 0.85);
            animation: rise 2.6s infinite ease-in;
        }
        .b2 {
            animation-delay: 0.9s;
        }
        .b3 {
            animation-delay: 1.7s;
        }
    }

    @keyframes rise {
        from {
            transform: translateY(0);
            opacity: 0.9;
        }
        to {
            transform: translateY(-22px);
            opacity: 0;
        }
    }

    .wordmark {
        font-size: 2rem;
        font-weight: 700;
        letter-spacing: -0.02em;

        em {
            font-style: normal;
            background: linear-gradient(120deg, var(--accent), var(--accent-2));
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
        }
    }

    h1 {
        position: relative;
        font-size: clamp(2.2rem, 5.5vw, 3.6rem);
        font-weight: 800;
        line-height: 1.1;
        letter-spacing: -0.03em;
        margin: 0 0 1.2rem;
    }

    .sub {
        position: relative;
        max-width: 34rem;
        font-size: 1.08rem;
        line-height: 1.6;
        opacity: 0.8;
        margin: 0 0 2.2rem;
    }

    .cta-row {
        position: relative;
        display: flex;
        gap: 0.9rem;
        flex-wrap: wrap;
        justify-content: center;
        margin-bottom: 2.6rem;
    }

    .cta {
        border: none;
        border-radius: 999px;
        padding: 0.85rem 2rem;
        font-size: 1.05rem;
        font-weight: 700;
        color: #fff;
        cursor: pointer;
        background: linear-gradient(120deg, var(--accent), var(--accent-2));
        box-shadow: 0 6px 20px rgba(69, 160, 90, 0.3);
        transition:
            transform 0.15s ease,
            box-shadow 0.15s ease;

        &:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 26px rgba(69, 160, 90, 0.4);
        }
        &:active {
            transform: translateY(0);
        }
    }

    .chips {
        position: relative;
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 0.5rem;
        max-width: 44rem;
    }

    .chip {
        font-size: 0.82rem;
        padding: 0.35rem 0.8rem;
        border-radius: 999px;
        border: 1px solid var(--border);
        background: var(--canvas-elevated, var(--canvas));
        opacity: 0.85;

        b {
            color: var(--accent);
        }
    }

    footer {
        text-align: center;
        padding: 1rem 1.5rem 2.5rem;
        font-size: 0.85rem;
        opacity: 0.55;
    }
</style>
