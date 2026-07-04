// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

// Persisted Ankidote state. This is stored in the collection config on the
// Python side (aqt/mediasrv.py -> col.set_config("ankidote", ...)) as the local
// working copy, and mirrored per-user to Firebase/Firestore when signed in (see
// aqt/ankidote/sync.py). sessionStorage is still used as a fast, same-session
// fallback before a save round-trips.

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

// Weekly practice-problem progress behind the Antidote vial (see
// pylib/anki/ankidote/plan_projection.py). Liquid level = done/target; the
// bubble shows the committed problems/hour rate; the caption shows the study
// time left to finish the week's problems at that pace.
export interface AnkidotePlanVial {
    healthPct: number;
    done: number;
    target: number;
    remaining: number;
    rate: number;
    hoursLeft: number;
    weekComplete: boolean;
}

// Weekly practice-problem quota derived from the chosen problems/hr pace.
export interface AnkidoteQuota {
    target: number;
    done: number;
    remaining: number;
    pace: string;
}

export interface AnkidoteScoreSnapshot {
    ts: number;
    low: number;
    high: number;
    baseline: number;
}

export interface AnkidoteState {
    diagnostic?: AnkidoteDiagnostic;
    plan?: AnkidotePlan;
    // Append-only history of diagnostic score snapshots, used to draw a real
    // progress trend on the My Brew page. Purely client-maintained.
    scoreHistory?: AnkidoteScoreSnapshot[];
    // True when signed in to Firebase. Signed-out users see the login screen.
    loggedIn?: boolean;
}

export interface AnkidoteAuthState {
    loggedIn: boolean;
    email?: string | null;
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

/**
 * Load real dashboard inputs (Anki card retention + practice tallies).
 *
 * This now calls the Rust `getAnkidoteStats` RPC via the transport-agnostic
 * engine client, so the desktop and Android paths are identical.
 */
export async function loadAnkidoteStats(): Promise<AnkidoteStats | null> {
    try {
        const { loadAnkidoteStats: fromEngine } = await import("./engine");
        return await fromEngine();
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

/** Current Firebase auth state. */
export async function loadAnkidoteAuth(): Promise<AnkidoteAuthState> {
    try {
        return (await postJson("ankidoteAuthState", {})) as AnkidoteAuthState;
    } catch {
        return { loggedIn: false };
    }
}

/** Sign in (or sign up) with email/password. Returns error message or null. */
export async function ankidoteLogin(
    email: string,
    password: string,
    create: boolean,
): Promise<{ ok: boolean; error?: string }> {
    try {
        return (await postJson("ankidoteAuthLogin", {
            email,
            password,
            create,
        })) as { ok: boolean; error?: string };
    } catch (err) {
        return { ok: false, error: `${err}` };
    }
}

/** Sign out of Firebase. */
export async function ankidoteLogout(): Promise<void> {
    try {
        await postJson("ankidoteAuthLogout", {});
    } catch {
        // best-effort
    }
}
