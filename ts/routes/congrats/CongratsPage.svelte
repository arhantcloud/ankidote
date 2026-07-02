<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import type { CongratsInfoResponse } from "@generated/anki/scheduler_pb";
    import { congratsInfo } from "@generated/backend";
    import * as tr from "@generated/ftl";
    import { bridgeLink } from "@tslib/bridgecommand";

    import Col from "$lib/components/Col.svelte";
    import Container from "$lib/components/Container.svelte";

    import { buildNextLearnMsg } from "./lib";
    import { onMount } from "svelte";

    export let info: CongratsInfoResponse;
    export let refreshPeriodically = true;

    const congrats = tr.schedulingCongratulationsFinished();
    let nextLearnMsg: string;
    $: nextLearnMsg = buildNextLearnMsg(info);
    const today_reviews = tr.schedulingTodayReviewLimitReached();
    const today_new = tr.schedulingTodayNewLimitReached();

    const unburyThem = bridgeLink("unbury", tr.schedulingUnburyThem());
    const buriedMsg = tr.schedulingBuriedCardsFound({ unburyThem });

    // If this deck belongs to Ankidote, offer a link back into the study loop.
    let ankidoteLoopMsg = "";
    async function checkAnkidote(): Promise<void> {
        try {
            const resp = await fetch("/_anki/ankidoteActive", {
                method: "POST",
                headers: { "Content-Type": "application/binary" },
                body: "{}",
            });
            if (!resp.ok) {
                return;
            }
            const data = (await resp.json()) as { isTopicDeck: boolean };
            if (data.isTopicDeck) {
                ankidoteLoopMsg = bridgeLink(
                    "ankidote:loop",
                    "← Back to your Ankidote study loop",
                );
            }
        } catch {
            // Not in an Ankidote context; ignore.
        }
    }

    onMount(() => {
        checkAnkidote();
        if (refreshPeriodically) {
            setInterval(async () => {
                try {
                    info = await congratsInfo({}, { alertOnError: false });
                } catch {
                    console.log("congrats fetch failed");
                }
            }, 60000);
        }
    });
</script>

<Container --gutter-block="1rem" --gutter-inline="2px" breakpoint="sm">
    <Col --col-justify="center">
        <div class="congrats">
            <h1>{congrats}</h1>

            {#if ankidoteLoopMsg}
                <p class="ankidote-loop">{@html ankidoteLoopMsg}</p>
            {/if}

            <p>{nextLearnMsg}</p>

            {#if info.reviewRemaining}
                <p>{today_reviews}</p>
            {/if}

            {#if info.newRemaining}
                <p>{today_new}</p>
            {/if}

            {#if info.bridgeCommandsSupported}
                {#if info.haveSchedBuried || info.haveUserBuried}
                    <p>
                        {@html buriedMsg}
                    </p>
                {/if}
            {/if}
        </div>
    </Col>
</Container>

<style lang="scss">
    .congrats {
        margin-top: 2em;
        max-width: 30em;
        font-size: var(--font-size);

        :global(a) {
            color: var(--fg-link);
            text-decoration: none;
        }
    }

    .ankidote-loop {
        font-weight: 600;
    }
</style>
