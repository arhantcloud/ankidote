// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

// Persisted Ankidote state. This is stored in the collection config on the
// Python side (aqt/mediasrv.py -> col.set_config("ankidote", ...)), so it saves
// locally and syncs to AnkiWeb like any other collection data. sessionStorage
// is still used as a fast, same-session fallback before a save round-trips.

export interface AnkidoteDiagnostic {
    baseline: number;
    low: number;
    high: number;
    answered: number;
    topicScores: unknown[];
    takenAt?: number;
}

export interface AnkidotePlan {
    baseline: number;
    desiredScore: number;
    weeklyBudget: number;
    testDate: string;
    totalHours: number;
    weeksNeeded: number;
    predictedLow: number;
    predictedHigh: number;
    commitments: { id: string; enabled: boolean; pace?: string }[];
}

export interface AnkidoteState {
    diagnostic?: AnkidoteDiagnostic;
    plan?: AnkidotePlan;
    // True when signed in to AnkiWeb. When logged out the returned state comes
    // from a local-only scratch (onboarding), and studying is unavailable.
    loggedIn?: boolean;
}

export interface AnkidotePracticeSession {
    daysAgo: number;
    topic: string;
    count: number;
    accuracy: number;
}

export interface AnkidoteTopicMastery {
    topic: string;
    section: string;
    mastered: number;
    total: number;
    pct: number;
}

export interface AnkidoteStats {
    problemsAnswered: number;
    problemsCorrect: number;
    sessions: AnkidotePracticeSession[];
    gradedReviews: number;
    topicMastery: AnkidoteTopicMastery[];
    memory: {
        masteryPct: number | null;
        masteredCards: number;
        totalCards: number;
        reviews: number;
    };
}

// application/binary is required by the API's CSRF guard even for a JSON body;
// the auth header is injected for this trusted webview by AuthInterceptor.
async function postJson(endpoint: string, body: unknown): Promise<unknown> {
    const resp = await fetch(`/_anki/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/binary" },
        body: JSON.stringify(body ?? {}),
    });
    if (!resp.ok) {
        throw new Error(await resp.text());
    }
    const text = await resp.text();
    return text ? JSON.parse(text) : {};
}

/** Load persisted (synced) Ankidote state from the collection config. */
export async function loadAnkidoteState(): Promise<AnkidoteState> {
    try {
        return ((await postJson("ankidoteStateGet", {})) as AnkidoteState) ?? {};
    } catch {
        return {};
    }
}

/** Load real dashboard inputs (Anki card retention + practice tallies). */
export async function loadAnkidoteStats(): Promise<AnkidoteStats | null> {
    try {
        return (await postJson("ankidoteStats", {})) as AnkidoteStats;
    } catch {
        return null;
    }
}

/** Merge-save part of the Ankidote state (e.g. just the diagnostic or plan). */
export async function saveAnkidoteState(partial: AnkidoteState): Promise<void> {
    try {
        await postJson("ankidoteStateSet", partial);
    } catch {
        // Persistence is best-effort; the in-session sessionStorage copy still
        // carries the flow if the backend is unreachable.
    }
}
